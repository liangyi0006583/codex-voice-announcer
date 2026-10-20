import Foundation
import AVFoundation
import CoreAudio

struct AudioPlan: Decodable {
    let file: String
    let rate: Double?
    let pitchSemitones: Double?
    let style: String?
    let settingsPath: String?
    let progressPath: String?
    let output: String?
    let muted: Bool?
}

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8)); exit(2)
}

func effects(rate: Double, pitch: Double, style: String) -> (Float, Float) {
    // Base cloning has no instruction/emotion channel. These are explicit audio effects.
    let tempo = style == "gentle" ? 0.96 : (style == "firm" ? 1.04 : 1.0)
    let tone = style == "gentle" ? 0.65 : (style == "firm" ? -1.1 : 0.0)
    return (Float(min(1.6, max(0.6, rate / 190 * tempo))),
            Float(min(500, max(-500, (pitch + tone) * 100))))
}

final class FilePlayer {
    let plan: AudioPlan
    let file: AVAudioFile
    let engine = AVAudioEngine()
    let node = AVAudioPlayerNode()
    let pitch = AVAudioUnitTimePitch()
    var cursor: AVAudioFramePosition = 0
    var startFrame: AVAudioFramePosition = 0
    var generation = 0
    var retries = 0
    var recovering = false
    var lastAdvance = Date()
    var lastProgress = Date.distantPast
    var effectsUpdates = 0
    var timer: Timer?
    var observer: NSObjectProtocol?
    var routeListener: AudioObjectPropertyListenerBlock?
    var address = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyDefaultOutputDevice,
                                            mScope: kAudioObjectPropertyScopeGlobal,
                                            mElement: kAudioObjectPropertyElementMain)

    init(_ plan: AudioPlan) throws {
        self.plan = plan
        file = try AVAudioFile(forReading: URL(fileURLWithPath: plan.file))
        engine.attach(node); engine.attach(pitch)
        connect()
        updateEffects()
        engine.mainMixerNode.outputVolume = plan.muted == true ? 0 : 1
    }

    func connect() {
        engine.connect(node, to: pitch, format: file.processingFormat)
        engine.connect(pitch, to: engine.mainMixerNode, format: file.processingFormat)
    }

    func progress(_ phase: String) {
        guard let path = plan.progressPath else { return }
        let value: [String: Any] = ["pid": getppid(), "playerPid": getpid(), "phase": phase,
            "updated": Date().timeIntervalSince1970, "rateMultiplier": pitch.rate,
            "pitchCents": pitch.pitch, "frame": cursor, "routeRecoveries": retries,
            "effectsUpdates": effectsUpdates]
        if let data = try? JSONSerialization.data(withJSONObject: value) {
            try? data.write(to: URL(fileURLWithPath: path), options: .atomic)
            chmod(path, 0o600)
        }
    }

    func updateEffects() {
        var rate = plan.rate ?? 190
        var tone = plan.pitchSemitones ?? 0
        var style = plan.style ?? "formal"
        if let path = plan.settingsPath, let data = try? Data(contentsOf: URL(fileURLWithPath: path)),
           let settings = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] {
            rate = (settings["rate"] as? NSNumber)?.doubleValue ?? rate
            tone = (settings["pitch_semitones"] as? NSNumber)?.doubleValue ?? tone
            style = settings["speech_style"] as? String ?? style
        }
        let value = effects(rate: rate, pitch: tone, style: style)
        // Repeatedly writing unchanged AU parameters can retrigger DSP smoothing.
        if abs(pitch.rate - value.0) > 0.0001 { pitch.rate = value.0; effectsUpdates += 1 }
        if abs(pitch.pitch - value.1) > 0.01 { pitch.pitch = value.1; effectsUpdates += 1 }
    }

    func captureCursor() {
        guard !recovering, let time = node.lastRenderTime,
              let playing = node.playerTime(forNodeTime: time) else { return }
        let next = min(file.length, startFrame + AVAudioFramePosition(Double(playing.sampleTime) * file.processingFormat.sampleRate / playing.sampleRate))
        if next > cursor { cursor = next; lastAdvance = Date() }
    }

    func schedule() throws {
        guard cursor < file.length else { progress("complete"); exit(0) }
        generation += 1
        let token = generation
        startFrame = cursor
        node.scheduleSegment(file, startingFrame: cursor, frameCount: AVAudioFrameCount(file.length - cursor),
                             at: nil, completionCallbackType: .dataPlayedBack) { [weak self] _ in
            DispatchQueue.main.async {
                guard let self = self, self.generation == token, !self.recovering else { return }
                self.cursor = self.file.length; self.progress("complete"); exit(0)
            }
        }
        try engine.start()
        node.play()
        recovering = false; lastAdvance = Date()
        progress("playing")
    }

    func recover() {
        guard !recovering else { return }
        captureCursor()
        recovering = true; generation += 1
        progress("switching_output")
        node.stop(); engine.stop()
        // A device can change both sample rate and channel count. Rebuild at file rate;
        // the mixer adapts to the new hardware rate rather than reusing a stale stream.
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { [weak self] in self?.restart() }
    }

    func restart() {
        retries += 1
        guard retries <= 12 else { fail("音频输出切换后无法恢复，请检查系统声音输出") }
        engine.reset()
        engine.disconnectNodeOutput(node); engine.disconnectNodeOutput(pitch)
        connect(); updateEffects()
        do { try schedule() }
        catch {
            node.stop(); engine.stop(); generation += 1; recovering = true
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { [weak self] in self?.restart() }
        }
    }

    func play() {
        observer = NotificationCenter.default.addObserver(forName: .AVAudioEngineConfigurationChange,
            object: engine, queue: .main) { [weak self] _ in self?.recover() }
        let listener: AudioObjectPropertyListenerBlock = { [weak self] _, _ in self?.recover() }
        routeListener = listener
        AudioObjectAddPropertyListenerBlock(AudioObjectID(kAudioObjectSystemObject), &address, .main, listener)
        do { try schedule() } catch { recover() }
        timer = Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { [weak self] _ in
            guard let self = self else { return }
            self.updateEffects(); self.captureCursor()
            if !self.recovering, Date().timeIntervalSince(self.lastAdvance) > 2 { self.recover() }
            if Date().timeIntervalSince(self.lastProgress) >= 0.5 {
                self.progress(self.recovering ? "switching_output" : "playing")
                self.lastProgress = Date()
            }
        }
        if CommandLine.arguments.contains("--simulate-route-change") {
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.8) { [weak self] in self?.recover() }
        }
    }

    func render(_ path: String) throws {
        let format = file.processingFormat
        try engine.enableManualRenderingMode(.offline, format: format, maximumFrameCount: 4096)
        let output = try AVAudioFile(forWriting: URL(fileURLWithPath: path), settings: [
            AVFormatIDKey: kAudioFormatLinearPCM, AVSampleRateKey: format.sampleRate,
            AVNumberOfChannelsKey: format.channelCount, AVLinearPCMBitDepthKey: 16,
            AVLinearPCMIsFloatKey: false, AVLinearPCMIsBigEndianKey: false])
        let buffer = AVAudioPCMBuffer(pcmFormat: engine.manualRenderingFormat, frameCapacity: 4096)!
        node.scheduleFile(file, at: nil)
        try engine.start(); node.play()
        let count = AVAudioFramePosition(ceil(Double(file.length) / Double(pitch.rate)))
        var stalled = 0
        while engine.manualRenderingSampleTime < count {
            let frames = min(4096, AVAudioFrameCount(count - engine.manualRenderingSampleTime))
            let status = try engine.renderOffline(frames, to: buffer)
            switch status {
            case .success: try output.write(from: buffer); stalled = 0
            case .cannotDoInCurrentContext: stalled += 1
            case .insufficientDataFromInputNode, .error: throw NSError(domain: "AudioPlayer", code: 3)
            @unknown default: throw NSError(domain: "AudioPlayer", code: 4)
            }
            if stalled > 100 { throw NSError(domain: "AudioPlayer", code: 5) }
        }
        engine.stop()
    }
}

let plan: AudioPlan
do { plan = try JSONDecoder().decode(AudioPlan.self, from: FileHandle.standardInput.readDataToEndOfFile()) }
catch { fail("播放器参数格式错误：\(error)") }
var player: FilePlayer!
do {
    player = try FilePlayer(plan)
    if let output = plan.output { try player.render(output); exit(0) }
    player.play()
} catch { fail("音频播放失败：\(error)") }
RunLoop.main.run()

import Foundation
import AVFoundation

struct Segment: Decodable {
    let text: String
    let rateMultiplier: Float
    let pitch: Float
    let pause: Double
}
struct SpeechPlan: Decodable {
    let voiceId: String?
    let rate: Float
    let segments: [Segment]
}

if CommandLine.arguments.contains("--inventory") {
    let voices = AVSpeechSynthesisVoice.speechVoices().filter { $0.language.hasPrefix("zh") }
    let rows = voices.map { ["id": $0.identifier, "name": $0.name, "language": $0.language, "quality": $0.quality.rawValue] as [String: Any] }
    let data = try JSONSerialization.data(withJSONObject: rows, options: [.sortedKeys])
    FileHandle.standardOutput.write(data)
    exit(0)
}

let data = FileHandle.standardInput.readDataToEndOfFile()
let plan: SpeechPlan
do { plan = try JSONDecoder().decode(SpeechPlan.self, from: data) }
catch { FileHandle.standardError.write(Data("语音计划格式错误\n".utf8)); exit(2) }
guard !plan.segments.isEmpty else { exit(0) }
let chinese = AVSpeechSynthesisVoice.speechVoices().filter { $0.language.hasPrefix("zh") }
let preferred = plan.voiceId.flatMap { AVSpeechSynthesisVoice(identifier: $0) }
let voiceCandidate = preferred ?? chinese.sorted { $0.quality.rawValue > $1.quality.rawValue }.first
let selectedVoice: AVSpeechSynthesisVoice = {
    guard let value = voiceCandidate else { FileHandle.standardError.write(Data("没有可用的中文音色\n".utf8)); exit(3) }
    return value
}()

func utterance(_ segment: Segment) -> AVSpeechUtterance {
    let u = AVSpeechUtterance(string: segment.text)
    u.voice = selectedVoice
    // The UI uses words/minute; AVFoundation uses a normalized rate.
    u.rate = min(0.61, max(0.36, 0.49 * plan.rate / 190 * segment.rateMultiplier))
    u.pitchMultiplier = min(1.34, max(0.74, segment.pitch))
    u.volume = CommandLine.arguments.contains("--validate-player") ? 0 : 1
    return u
}

class Player: NSObject, AVSpeechSynthesizerDelegate, @unchecked Sendable {
    let synth = AVSpeechSynthesizer()
    var remaining: Int
    init(_ segments: [Segment]) {
        remaining = segments.count
        super.init()
        synth.delegate = self
        for segment in segments {
            let u = utterance(segment)
            u.postUtteranceDelay = segment.pause
            synth.speak(u)
        }
    }
    func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        remaining -= 1
        if remaining == 0 { DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) { exit(0) } }
    }
    func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didCancel utterance: AVSpeechUtterance) { exit(4) }
}

class Renderer {
    let synth = AVSpeechSynthesizer()
    var index = 0
    var file: AVAudioFile?
    let destination: URL
    init(_ path: String) { destination = URL(fileURLWithPath: path) }
    func next() {
        guard index < plan.segments.count else { file = nil; exit(0) }
        let segment = plan.segments[index]
        synth.write(utterance(segment)) { [self] buffer in
            guard let pcm = buffer as? AVAudioPCMBuffer else { return }
            do {
                if pcm.frameLength > 0 {
                    if file == nil { file = try AVAudioFile(forWriting: destination, settings: pcm.format.settings, commonFormat: pcm.format.commonFormat, interleaved: pcm.format.isInterleaved) }
                    try file?.write(from: pcm)
                } else {
                    if let format = file?.processingFormat, let silence = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(format.sampleRate * segment.pause)) {
                        silence.frameLength = silence.frameCapacity
                        let buffers = UnsafeMutableAudioBufferListPointer(silence.mutableAudioBufferList)
                        for buffer in buffers { if let ptr = buffer.mData { memset(ptr, 0, Int(buffer.mDataByteSize)) } }
                        try file?.write(from: silence)
                    }
                    index += 1
                    DispatchQueue.main.async { self.next() }
                }
            } catch {
                FileHandle.standardError.write(Data("音频文件生成失败：\(error)\n".utf8)); exit(5)
            }
        }
    }
}

var renderer: Renderer?
var player: Player?
if let position = CommandLine.arguments.firstIndex(of: "--output"), CommandLine.arguments.count > position + 1 {
    renderer = Renderer(CommandLine.arguments[position + 1]); renderer!.next()
} else {
    player = Player(plan.segments)
}
RunLoop.main.run()

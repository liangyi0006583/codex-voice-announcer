import AppKit

let dataDir = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/CodexVoiceAnnouncer")
let serviceLabel = "io.github.codex-voice-announcer.monitor"

func loadJSON(_ name: String) -> Any? {
    guard let data = try? Data(contentsOf: dataDir.appendingPathComponent(name)) else { return nil }
    return try? JSONSerialization.jsonObject(with: data)
}

func saveJSON(_ name: String, _ value: Any) {
    guard let data = try? JSONSerialization.data(withJSONObject: value, options: [.prettyPrinted, .sortedKeys]) else { return }
    try? data.write(to: dataDir.appendingPathComponent(name), options: .atomic)
    try? FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: dataDir.appendingPathComponent(name).path)
}

func command(_ action: String, _ id: String? = nil) {
    var value: [String: Any] = ["action": action]
    if let id = id { value["id"] = id }
    saveJSON("controls/\(UUID().uuidString).json", value)
}

@discardableResult func runLaunchctl(_ arguments: [String]) -> Int32 {
    let task = Process()
    task.executableURL = URL(fileURLWithPath: "/bin/launchctl")
    task.arguments = arguments
    task.standardOutput = FileHandle.nullDevice
    task.standardError = FileHandle.nullDevice
    do { try task.run(); task.waitUntilExit(); return task.terminationStatus }
    catch { return -1 }
}

class AppDelegate: NSObject, NSApplicationDelegate, NSTableViewDataSource, NSTableViewDelegate, NSWindowDelegate {
    var window: NSWindow!
    var statusItem: NSStatusItem!
    var status = NSTextField(labelWithString: "正在连接…")
    var subtitle = NSTextField(labelWithString: "")
    var pauseButton = NSButton(title: "暂停播报", target: nil, action: nil)
    var completionBox = NSButton(checkboxWithTitle: "任务完成时播报结果", target: nil, action: nil)
    var table = NSTableView()
    var detail = NSTextView()
    var entries: [[String: Any]] = []
    var timer: Timer?
    var selectedID: String?
    var paused = false
    var menuPause: NSMenuItem!
    var rateSlider = NSSlider(value: 190, minValue: 120, maxValue: 280, target: nil, action: nil)
    var rateLabel = NSTextField(labelWithString: "语速：190")
    var voicePicker = NSPopUpButton()
    var stylePicker = NSPopUpButton()
    var toneSlider = NSSlider(value: 0, minValue: -3, maxValue: 3, target: nil, action: nil)
    var toneLabel = NSTextField(labelWithString: "音调：0 半音")
    let styles = ["formal", "gentle", "firm"]
    let voiceIDs = ["local:custom-1", "local:custom-2", "com.apple.ttsbundle.gryphon-neural_Shufen_zh-TW_premium", "com.apple.voice.compact.zh-CN.Tingting"]

    func applicationDidFinishLaunching(_ notification: Notification) {
        try? FileManager.default.createDirectory(at: dataDir.appendingPathComponent("controls"), withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        if loadJSON("settings.json") == nil {
            saveJSON("settings.json", ["paused": false, "announce_completed": true, "voice": "Tingting", "rate": 190, "speech_limit": 500, "speech_style": "formal", "voice_id": voiceIDs[3]])
        }
        startService()
        makeWindow()
        makeMenu()
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { [weak self] _ in self?.refresh() }
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func startService() {
        let domain = "gui/\(getuid())"
        let target = "\(domain)/\(serviceLabel)"
        let plist = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/LaunchAgents/\(serviceLabel).plist").path
        let disabled = dataDir.appendingPathComponent("\(serviceLabel).plist.disabled").path
        if !FileManager.default.fileExists(atPath: plist), FileManager.default.fileExists(atPath: disabled) {
            try? FileManager.default.moveItem(atPath: disabled, toPath: plist)
        }
        // kickstart without -k does not interrupt a running announcer.
        if runLaunchctl(["kickstart", target]) != 0 {
            _ = runLaunchctl(["bootstrap", domain, plist])
            _ = runLaunchctl(["kickstart", target])
        }
    }

    func makeWindow() {
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 860, height: 680), styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "Codex 语音播报"
        window.minSize = NSSize(width: 760, height: 630)
        window.center()
        window.delegate = self
        let root = NSStackView()
        root.orientation = .vertical
        root.alignment = .leading
        root.spacing = 14
        root.edgeInsets = NSEdgeInsets(top: 24, left: 24, bottom: 22, right: 24)
        root.translatesAutoresizingMaskIntoConstraints = false
        window.contentView!.addSubview(root)
        NSLayoutConstraint.activate([
            root.leadingAnchor.constraint(equalTo: window.contentView!.leadingAnchor),
            root.trailingAnchor.constraint(equalTo: window.contentView!.trailingAnchor),
            root.topAnchor.constraint(equalTo: window.contentView!.topAnchor),
            root.bottomAnchor.constraint(equalTo: window.contentView!.bottomAnchor)
        ])
        let title = NSTextField(labelWithString: "需要你处理时，读给你听")
        title.font = .systemFont(ofSize: 25, weight: .semibold)
        root.addArrangedSubview(title)
        status.font = .systemFont(ofSize: 16, weight: .medium)
        root.addArrangedSubview(status)
        subtitle.font = .systemFont(ofSize: 12)
        subtitle.textColor = .secondaryLabelColor
        root.addArrangedSubview(subtitle)

        let buttons = NSStackView()
        buttons.orientation = .horizontal
        buttons.spacing = 10
        pauseButton.target = self; pauseButton.action = #selector(togglePause)
        buttons.addArrangedSubview(pauseButton)
        buttons.addArrangedSubview(button("测试声音", #selector(testSpeech)))
        buttons.addArrangedSubview(button("停止当前朗读", #selector(stopSpeech)))
        buttons.addArrangedSubview(button("重新连接", #selector(restartService)))
        root.addArrangedSubview(buttons)

        let options = NSStackView()
        options.orientation = .horizontal; options.spacing = 16
        completionBox.target = self; completionBox.action = #selector(changeCompletion)
        options.addArrangedSubview(completionBox)
        rateSlider.target = self; rateSlider.action = #selector(changeRate)
        rateSlider.isContinuous = false
        rateSlider.widthAnchor.constraint(equalToConstant: 150).isActive = true
        options.addArrangedSubview(rateLabel)
        options.addArrangedSubview(rateSlider)
        root.addArrangedSubview(options)
        let voiceOptions = NSStackView(); voiceOptions.orientation = .horizontal; voiceOptions.spacing = 12
        voiceOptions.addArrangedSubview(NSTextField(labelWithString: "音色"))
        voicePicker.addItems(withTitles: ["自定义参考音色一", "自定义参考音色二", "增强系统女声", "标准普通话女声（大陆）"])
        voicePicker.target = self; voicePicker.action = #selector(changeVoice)
        voiceOptions.addArrangedSubview(voicePicker)
        voiceOptions.addArrangedSubview(NSTextField(labelWithString: "语气"))
        stylePicker.addItems(withTitles: ["清晰正式", "柔和自然", "沉稳坚定"])
        stylePicker.target = self; stylePicker.action = #selector(changeStyle)
        voiceOptions.addArrangedSubview(stylePicker)
        root.addArrangedSubview(voiceOptions)
        let toneOptions = NSStackView(); toneOptions.orientation = .horizontal; toneOptions.spacing = 16
        toneOptions.addArrangedSubview(toneLabel)
        toneSlider.target = self; toneSlider.action = #selector(changeTone)
        toneSlider.isContinuous = false
        toneSlider.widthAnchor.constraint(equalToConstant: 180).isActive = true
        toneOptions.addArrangedSubview(toneSlider)
        root.addArrangedSubview(toneOptions)
        let info = NSTextField(labelWithString: "角色语音先准备再连续播放 · 语速与音调播放中可调 · 语气调整音调、节奏与停顿")
        info.font = .systemFont(ofSize: 12); info.textColor = .secondaryLabelColor
        root.addArrangedSubview(info)

        let heading = NSTextField(labelWithString: "最近提醒")
        heading.font = .systemFont(ofSize: 15, weight: .semibold)
        root.addArrangedSubview(heading)
        for (id, name, width) in [("time", "时间", CGFloat(100)), ("title", "任务", CGFloat(390)), ("kind", "状态", CGFloat(155)), ("speech_status", "播报", CGFloat(95))] {
            let column = NSTableColumn(identifier: NSUserInterfaceItemIdentifier(id))
            column.title = name; column.width = width
            table.addTableColumn(column)
        }
        table.delegate = self; table.dataSource = self
        table.rowHeight = 30
        table.usesAlternatingRowBackgroundColors = true
        let scroll = NSScrollView()
        scroll.hasVerticalScroller = true
        scroll.borderType = .bezelBorder
        scroll.documentView = table
        root.addArrangedSubview(scroll)
        scroll.widthAnchor.constraint(equalTo: root.widthAnchor, constant: -48).isActive = true
        scroll.heightAnchor.constraint(greaterThanOrEqualToConstant: 140).isActive = true
        scroll.setContentHuggingPriority(.defaultLow, for: .vertical)

        detail.isEditable = false; detail.isSelectable = true
        detail.font = .systemFont(ofSize: 14)
        detail.textContainerInset = NSSize(width: 10, height: 10)
        detail.string = "选择一条提醒，查看具体内容。已有未读任务会补播；已读的历史结果不补播。"
        let detailScroll = NSScrollView()
        detailScroll.hasVerticalScroller = true; detailScroll.borderType = .bezelBorder
        detailScroll.documentView = detail
        root.addArrangedSubview(detailScroll)
        detailScroll.widthAnchor.constraint(equalTo: root.widthAnchor, constant: -48).isActive = true
        detailScroll.heightAnchor.constraint(equalToConstant: 130).isActive = true
        let bottom = NSStackView(); bottom.orientation = .horizontal; bottom.spacing = 10
        bottom.addArrangedSubview(button("打开对应任务", #selector(openTask)))
        bottom.addArrangedSubview(button("重新朗读所选内容", #selector(replay)))
        bottom.addArrangedSubview(button("使用说明", #selector(showHelp)))
        root.addArrangedSubview(bottom)
    }

    func button(_ title: String, _ action: Selector) -> NSButton {
        let b = NSButton(title: title, target: self, action: action)
        b.bezelStyle = .rounded
        return b
    }

    func makeMenu() {
        let mainMenu = NSMenu()
        let appItem = NSMenuItem()
        let appMenu = NSMenu(title: "Codex语音播报")
        let appQuit = NSMenuItem(title: "退出控制窗口（后台继续播报）", action: #selector(quitUI), keyEquivalent: "q")
        appQuit.target = self; appMenu.addItem(appQuit)
        appItem.submenu = appMenu; mainMenu.addItem(appItem)
        NSApp.mainMenu = mainMenu
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.image = NSImage(systemSymbolName: "speaker.wave.2.fill", accessibilityDescription: "Codex 语音播报")
        let menu = NSMenu()
        let show = NSMenuItem(title: "打开播报窗口", action: #selector(showWindow), keyEquivalent: "")
        show.target = self; menu.addItem(show)
        menuPause = NSMenuItem(title: "暂停播报", action: #selector(togglePause), keyEquivalent: "")
        menuPause.target = self; menu.addItem(menuPause)
        let test = NSMenuItem(title: "测试声音", action: #selector(testSpeech), keyEquivalent: "")
        test.target = self; menu.addItem(test)
        menu.addItem(.separator())
        let quit = NSMenuItem(title: "退出控制窗口（后台继续播报）", action: #selector(quitUI), keyEquivalent: "q")
        quit.target = self; menu.addItem(quit)
        statusItem.menu = menu
    }

    func refresh() {
        let config = loadJSON("settings.json") as? [String: Any] ?? [:]
        paused = config["paused"] as? Bool ?? false
        pauseButton.title = paused ? "恢复播报" : "暂停播报"
        menuPause?.title = pauseButton.title
        completionBox.state = (config["announce_completed"] as? Bool ?? true) ? .on : .off
        let rate = config["rate"] as? Double ?? 190
        rateSlider.doubleValue = rate; rateLabel.stringValue = "语速：\(Int(rate))"
        let voiceId = config["voice_id"] as? String ?? voiceIDs[3]
        voicePicker.selectItem(at: voiceIDs.firstIndex(of: voiceId) ?? 0)
        let characterVoice = voiceId.hasPrefix("local:")
        rateSlider.isEnabled = true
        if characterVoice { rateLabel.stringValue = String(format: "语速：%.2f 倍", rate / 190) }
        stylePicker.selectItem(at: styles.firstIndex(of: config["speech_style"] as? String ?? "formal") ?? 0)
        let tone = config["pitch_semitones"] as? Double ?? 0
        toneSlider.doubleValue = tone
        toneLabel.stringValue = String(format: "音调：%+.1f 半音", tone)
        let s = loadJSON("status.json") as? [String: Any] ?? [:]
        let heartbeat = s["heartbeat"] as? Double ?? 0
        let alive = Date().timeIntervalSince1970 - heartbeat < 12
        status.stringValue = alive ? (paused ? "已暂停语音 · 继续记录提醒" : "播报已开启") + " · " + (s["connection"] as? String ?? "等待连接") : "后台未运行，请点重新连接"
        status.textColor = alive ? (paused ? .secondaryLabelColor : .systemGreen) : .systemOrange
        let warning = s["warning"] as? String ?? ""
        subtitle.stringValue = warning.isEmpty ? "待查看 \(s["unread_count"] as? Int ?? 0) 个 · 已登记 \(s["catalog_count"] as? Int ?? 0) 个任务 · 实时连接 \(s["live_count"] as? Int ?? 0) 个 · 排队 \(s["queue_count"] as? Int ?? 0) 条" : warning
        statusItem?.button?.toolTip = status.stringValue
        let selection = selectedID
        entries = loadJSON("history.json") as? [[String: Any]] ?? []
        table.reloadData()
        if let selection = selection, let row = entries.firstIndex(where: { $0["id"] as? String == selection }) {
            table.selectRowIndexes(IndexSet(integer: row), byExtendingSelection: false)
        }
    }

    func numberOfRows(in tableView: NSTableView) -> Int { entries.count }
    func tableView(_ tableView: NSTableView, viewFor tableColumn: NSTableColumn?, row: Int) -> NSView? {
        guard row < entries.count, let key = tableColumn?.identifier.rawValue else { return nil }
        var value = entries[row][key] as? String ?? ""
        if key == "time", value.count >= 19 { value = String(value.dropFirst(11).prefix(8)) }
        if key == "speech_status" {
            value = ["queued": "排队", "generating": "合成中", "speaking": "朗读中", "spoken": "已播报", "paused": "已暂停", "failed": "失败", "stopped": "已停止"][value] ?? value
        }
        let field = NSTextField(labelWithString: value)
        field.lineBreakMode = .byTruncatingTail
        field.toolTip = value
        return field
    }
    func tableViewSelectionDidChange(_ notification: Notification) {
        let row = table.selectedRow
        guard row >= 0 && row < entries.count else { return }
        let entry = entries[row]
        selectedID = entry["id"] as? String
        detail.string = "\(entry["title"] as? String ?? "")\n\(entry["kind"] as? String ?? "") · \(entry["time"] as? String ?? "")\n\n\(entry["content"] as? String ?? "")"
    }

    @objc func togglePause() {
        var cfg = loadJSON("settings.json") as? [String: Any] ?? [:]
        cfg["paused"] = !paused; saveJSON("settings.json", cfg)
        if !paused { command("stop_speech") }
        refresh()
    }
    @objc func changeCompletion() {
        var cfg = loadJSON("settings.json") as? [String: Any] ?? [:]
        cfg["announce_completed"] = completionBox.state == .on; saveJSON("settings.json", cfg)
    }
    @objc func changeRate() {
        var cfg = loadJSON("settings.json") as? [String: Any] ?? [:]
        cfg["rate"] = Int(rateSlider.doubleValue); saveJSON("settings.json", cfg)
        refresh()
    }
    @objc func changeVoice() {
        var cfg = loadJSON("settings.json") as? [String: Any] ?? [:]
        cfg["voice_id"] = voiceIDs[max(0, voicePicker.indexOfSelectedItem)]; saveJSON("settings.json", cfg)
    }
    @objc func changeStyle() {
        var cfg = loadJSON("settings.json") as? [String: Any] ?? [:]
        cfg["speech_style"] = styles[max(0, stylePicker.indexOfSelectedItem)]; saveJSON("settings.json", cfg)
    }
    @objc func changeTone() {
        var cfg = loadJSON("settings.json") as? [String: Any] ?? [:]
        cfg["pitch_semitones"] = (toneSlider.doubleValue * 10).rounded() / 10
        saveJSON("settings.json", cfg); refresh()
    }
    @objc func testSpeech() {
        if paused {
            let alert = NSAlert(); alert.messageText = "播报已暂停"; alert.informativeText = "先点恢复播报，再测试声音。"; alert.runModal(); return
        }
        startService(); command("test")
    }
    @objc func stopSpeech() { command("stop_speech") }
    @objc func restartService() {
        _ = runLaunchctl(["kickstart", "-k", "gui/\(getuid())/\(serviceLabel)"])
        startService(); refresh()
    }
    @objc func replay() { if let id = selectedID { command("replay", id) } }
    @objc func openTask() {
        let row = table.selectedRow
        guard row >= 0 && row < entries.count, let tid = entries[row]["thread_id"] as? String, !tid.isEmpty else { return }
        var components = URLComponents()
        components.scheme = "codex"; components.host = "threads"; components.path = "/\(tid)"
        if let host = entries[row]["host"] as? String, host != "local" { components.queryItems = [URLQueryItem(name: "hostId", value: host)] }
        if let url = components.url { NSWorkspace.shared.open(url) }
    }
    @objc func showHelp() {
        if let url = Bundle.main.url(forResource: "使用说明", withExtension: "txt") { NSWorkspace.shared.open(url) }
    }
    @objc func showWindow() { window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true) }
    @objc func quitUI() { NSApp.terminate(nil) }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool { showWindow(); return true }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
}

let application = NSApplication.shared
let delegate = AppDelegate()
application.delegate = delegate
application.setActivationPolicy(.regular)
application.run()

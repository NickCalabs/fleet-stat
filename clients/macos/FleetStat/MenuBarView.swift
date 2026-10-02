import SwiftUI

enum FleetTab: String, CaseIterable {
    case overview = "Overview"
    case sessions = "Sessions"
    case nodes = "Nodes"
    case library = "Library"
    case settings = "Settings"

    var icon: String {
        switch self {
        case .overview: return "square.grid.2x2"
        case .sessions: return "bubble.left.and.text.bubble.right"
        case .nodes: return "server.rack"
        case .library: return "books.vertical"
        case .settings: return "gear"
        }
    }
}

struct MenuBarView: View {
    @ObservedObject var service: FleetService
    @State private var selectedTab: FleetTab = .overview
    @State private var editingURL: String = ""
    @State private var showingURLSaved = false
    @AppStorage("showInDock") private var showInDock = false
    @AppStorage("refreshInterval") private var refreshInterval = 30.0

    private var maxContentHeight: CGFloat {
        (NSScreen.main?.visibleFrame.height ?? 800) - 120
    }

    var body: some View {
        VStack(spacing: 0) {
            if !service.data.reachable {
                unreachableView
            } else {
                headerView
                    .padding(.horizontal, 14)
                    .padding(.top, 12)
                    .padding(.bottom, 8)

                tabBar
                    .padding(.horizontal, 14)
                    .padding(.bottom, 8)

                Divider()

                tabContent
                    .padding(14)
                    .frame(minHeight: 200, maxHeight: maxContentHeight, alignment: .top)
                    .fixedSize(horizontal: false, vertical: true)
                    .animation(.easeInOut(duration: 0.2), value: selectedTab)

                Divider()
                footerView
                    .padding(.horizontal, 14)
                    .padding(.vertical, 8)
            }
        }
        .frame(width: 420)
        .background(.ultraThinMaterial)
        .onAppear { editingURL = service.baseURL ?? "" }
    }

    @ViewBuilder
    private var tabContent: some View {
        Group {
            switch selectedTab {
            case .overview: overviewSection
            case .sessions: sessionsSection
            case .nodes: nodesSection
            case .library: librarySection
            case .settings: settingsSection
            }
        }
        .transition(.opacity)
        .id(selectedTab)
    }

    // MARK: - Header

    private var headerView: some View {
        HStack(alignment: .firstTextBaseline) {
            VStack(alignment: .leading, spacing: 2) {
                Text("Fleet Stat")
                    .font(.system(size: 15, weight: .bold))
                Text("Updated just now")
                    .font(.system(size: 10))
                    .foregroundStyle(.secondary)
            }
            Spacer()
            statusPill
        }
    }

    private var statusPill: some View {
        let up = service.data.models.filter { $0.up ?? false }
        let active = service.data.sessions.filter { ($0.active ?? false) && !($0.utility ?? false) }
        let tps = up.reduce(0.0) { $0 + ($1.gen_tps ?? 0) }

        return HStack(spacing: 8) {
            HStack(spacing: 3) {
                Circle().fill(up.count == service.data.models.count ? .green : .red)
                    .frame(width: 6, height: 6)
                Text("\(up.count)/\(service.data.models.count)")
                    .font(.system(size: 11, weight: .medium, design: .monospaced))
            }
            if tps >= 1 {
                Text("\(Int(tps)) t/s")
                    .font(.system(size: 11, weight: .semibold, design: .monospaced))
                    .foregroundStyle(.green)
            }
            if !service.errorsLastHour.isEmpty {
                Text("\(service.errorsLastHour.count) err")
                    .font(.system(size: 11, weight: .semibold, design: .monospaced))
                    .foregroundStyle(.red)
                    .help("Errors in the last hour")
            }
            if !active.isEmpty {
                Text("\(active.count) active")
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 5)
        .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 6))
    }

    // MARK: - Tab Bar

    private var tabBar: some View {
        HStack(spacing: 2) {
            ForEach(FleetTab.allCases, id: \.self) { tab in
                Button {
                    withAnimation(.easeInOut(duration: 0.15)) { selectedTab = tab }
                } label: {
                    VStack(spacing: 3) {
                        Image(systemName: tab.icon)
                            .font(.system(size: 11))
                        Text(tab.rawValue)
                            .font(.system(size: 9, weight: .medium))
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 6)
                    .background(
                        selectedTab == tab
                            ? AnyShapeStyle(.tint.opacity(0.15))
                            : AnyShapeStyle(.clear),
                        in: RoundedRectangle(cornerRadius: 6)
                    )
                    .foregroundStyle(selectedTab == tab ? .primary : .secondary)
                }
                .buttonStyle(.plain)
            }
        }
    }

    // MARK: - Overview

    private var overviewSection: some View {
        let s = service.stats
        return VStack(alignment: .leading, spacing: 10) {
            if !service.data.events.isEmpty {
                recentEventsCard
            }

            // Usage header card
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Text("Usage · 7d")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(.secondary)
                    Spacer()
                }

                HStack(alignment: .firstTextBaseline, spacing: 0) {
                    Text("~\(fmtTokens(s.totalTokens7d))")
                        .font(.system(size: 22, weight: .bold, design: .rounded))
                    Text(" tokens")
                        .font(.system(size: 12))
                        .foregroundStyle(.secondary)
                }

                HStack(spacing: 16) {
                    statColumn("Today", value: fmtTokens(s.todayTokens))
                    statColumn("7d tokens", value: fmtTokens(s.totalTokens7d))
                    statColumn("Requests", value: "\(s.totalRequests7d)")
                    statColumn("Sessions", value: "\(s.totalSessions7d)")
                }
            }
            .padding(12)
            .background(.quaternary.opacity(0.3), in: RoundedRectangle(cornerRadius: 8))

            // Daily bar chart
            if !s.dailyStats.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Daily Tokens")
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundStyle(.secondary)
                    TokenBarChart(days: s.dailyStats)
                        .frame(height: 80)
                }
                .padding(12)
                .background(.quaternary.opacity(0.3), in: RoundedRectangle(cornerRadius: 8))
            }

            // Models breakdown
            VStack(alignment: .leading, spacing: 6) {
                Text("By Model")
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundStyle(.secondary)

                ForEach(service.data.models) { m in
                    let modelStats = s.byModel.first { $0.name == m.name }
                    HStack(spacing: 8) {
                        Circle()
                            .fill(modelColor(m))
                            .frame(width: 8, height: 8)
                        VStack(alignment: .leading, spacing: 1) {
                            Text(m.name)
                                .font(.system(size: 11, weight: .semibold))
                                .lineLimit(1)
                            if let ident = m.identity {
                                Text(identityString(ident))
                                    .font(.system(size: 9, design: .monospaced))
                                    .foregroundStyle(.secondary)
                            }
                        }
                        Spacer()
                        VStack(alignment: .trailing, spacing: 1) {
                            if !(m.up ?? false) {
                                statusBadge("DOWN", color: .red)
                            } else if (m.running ?? 0) > 0 {
                                statusBadge("\(Int(m.gen_tps ?? 0)) t/s", color: .green)
                            } else {
                                statusBadge("idle", color: .secondary)
                            }
                            if let ms = modelStats {
                                Text("\(fmtTokens(ms.tokens)) · \(ms.requests) req")
                                    .font(.system(size: 9, design: .monospaced))
                                    .foregroundStyle(.tertiary)
                            }
                        }
                    }
                    if let ms = modelStats, s.totalTokens7d > 0 {
                        FleetProgressBar(
                            value: ms.tokens / s.totalTokens7d,
                            color: modelColor(m) == .secondary ? .blue : modelColor(m),
                            height: 4
                        )
                    }
                }
            }
            .padding(12)
            .background(.quaternary.opacity(0.3), in: RoundedRectangle(cornerRadius: 8))

            // By harness
            VStack(alignment: .leading, spacing: 6) {
                Text("By Source")
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundStyle(.secondary)

                ForEach(s.byHarness, id: \.name) { h in
                    HStack {
                        Text(h.name)
                            .font(.system(size: 11, weight: .medium))
                        Spacer()
                        Text(fmtTokens(h.tokens))
                            .font(.system(size: 11, weight: .medium, design: .monospaced))
                        Text("·")
                            .foregroundStyle(.tertiary)
                        Text("\(h.requests) req")
                            .font(.system(size: 10, design: .monospaced))
                            .foregroundStyle(.secondary)
                    }
                    if s.totalTokens7d > 0 {
                        FleetProgressBar(
                            value: h.tokens / s.totalTokens7d,
                            color: .teal,
                            height: 4
                        )
                    }
                }
            }
            .padding(12)
            .background(.quaternary.opacity(0.3), in: RoundedRectangle(cornerRadius: 8))

            // Top model callout
            if let top = s.topModel {
                HStack {
                    Image(systemName: "trophy")
                        .font(.system(size: 10))
                        .foregroundStyle(.orange)
                    Text("Top model: \(top)")
                        .font(.system(size: 10))
                        .foregroundStyle(.secondary)
                }
            }
        }
    }

    private var recentEventsCard: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text("Recent Events")
                .font(.system(size: 10, weight: .semibold))
                .foregroundStyle(.secondary)
            ForEach(service.data.events) { e in
                HStack(spacing: 6) {
                    Circle()
                        .fill(e.isError ? Color.red : Color.orange)
                        .frame(width: 6, height: 6)
                    Text(timeString(e.ts))
                        .font(.system(size: 10, design: .monospaced))
                        .foregroundStyle(.secondary)
                    if let node = e.node {
                        Text(node)
                            .font(.system(size: 10, weight: .medium))
                    }
                    Text(e.title)
                        .font(.system(size: 10))
                        .foregroundStyle(e.isError ? .red : .primary)
                        .lineLimit(1)
                        .truncationMode(.tail)
                }
                .help(e.detail ?? e.title)
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.quaternary.opacity(0.3), in: RoundedRectangle(cornerRadius: 8))
    }

    private func statColumn(_ label: String, value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label)
                .font(.system(size: 9))
                .foregroundStyle(.tertiary)
            Text(value)
                .font(.system(size: 12, weight: .semibold, design: .monospaced))
        }
    }

    // MARK: - Sessions

    private var filteredSessions: [Session] {
        let real = service.data.sessions.filter { $0.isReal }
        return Array(
            real.sorted { a, b in
                if (a.active ?? false) != (b.active ?? false) { return a.active ?? false }
                return (a.last_ts ?? 0) > (b.last_ts ?? 0)
            }
            .prefix(10)
        )
    }

    private var sessionsSection: some View {
        VStack(alignment: .leading, spacing: 6) {
            if filteredSessions.isEmpty {
                emptyState(icon: "bubble.left", message: "No recent sessions")
            } else {
                ForEach(filteredSessions, id: \.stableID) { session in
                    sessionCard(session)
                }
            }
        }
    }

    private func sessionCard(_ s: Session) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Circle()
                    .fill((s.active ?? false) ? Color.green : Color.secondary.opacity(0.5))
                    .frame(width: 8, height: 8)
                VStack(alignment: .leading, spacing: 1) {
                    HStack(spacing: 4) {
                        Text(truncate(s.title ?? "\(s.model ?? "?") session", to: 30))
                            .font(.system(size: 11, weight: .medium))
                            .lineLimit(1)
                        if s.reported ?? false {
                            Image(systemName: "checkmark.seal.fill")
                                .font(.system(size: 9))
                                .foregroundStyle(.blue)
                                .help("Title reported by the harness")
                        }
                    }
                    if let label = s.harness_label ?? s.harness {
                        Text(label)
                            .font(.system(size: 9))
                            .foregroundStyle(.tertiary)
                    }
                }
                Spacer()
                VStack(alignment: .trailing, spacing: 1) {
                    Text(agoString(s.last_ts))
                        .font(.system(size: 10, design: .monospaced))
                        .foregroundStyle(.secondary)
                    if let tok = s.tokens_total, tok > 0 {
                        Text(fmtTokens(tok))
                            .font(.system(size: 9, design: .monospaced))
                            .foregroundStyle(.tertiary)
                    }
                }
            }
            if let pct = s.fill_pct {
                VStack(alignment: .leading, spacing: 3) {
                    FleetProgressBar(
                        value: pct / 100,
                        color: pct >= 80 ? .orange : .blue
                    )
                    Text("\(fmtNum(s.ctx_tokens)) / \(fmtNum(s.ctx_ceiling)) tokens (\(Int(pct))%)")
                        .font(.system(size: 9, design: .monospaced))
                        .foregroundStyle(pct >= 80 ? .orange : .secondary)
                }
                .padding(.leading, 16)
            }
        }
        .padding(10)
        .background(.quaternary.opacity(0.3), in: RoundedRectangle(cornerRadius: 8))
    }

    // MARK: - Nodes

    private var gpuNodes: [Node] {
        service.data.nodes.filter { n in
            n.gpu != nil || n.up == false || (n.mem_pct ?? 0) > 0
        }
    }

    private var nodesSection: some View {
        VStack(alignment: .leading, spacing: 6) {
            if gpuNodes.isEmpty {
                emptyState(icon: "server.rack", message: "No nodes reporting")
            } else {
                ForEach(gpuNodes) { node in
                    nodeCard(node)
                }
            }
        }
    }

    private func nodeCard(_ n: Node) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Circle()
                    .fill(n.up == false ? Color.red : Color.green)
                    .frame(width: 8, height: 8)
                Text(n.label)
                    .font(.system(size: 12, weight: .semibold))
                Spacer()
                if n.up == false {
                    statusBadge("DOWN", color: .red)
                }
            }

            if n.up != false {
                if let g = n.gpu {
                    VStack(alignment: .leading, spacing: 4) {
                        HStack {
                            Text("GPU")
                                .font(.system(size: 9, weight: .semibold))
                                .foregroundStyle(.secondary)
                            Spacer()
                            Text("\(Int(g.util_pct ?? 0))%")
                                .font(.system(size: 10, weight: .medium, design: .monospaced))
                        }
                        FleetProgressBar(
                            value: (g.util_pct ?? 0) / 100,
                            color: (g.util_pct ?? 0) >= 80 ? .orange : .green
                        )

                        HStack {
                            Text("VRAM")
                                .font(.system(size: 9, weight: .semibold))
                                .foregroundStyle(.secondary)
                            Spacer()
                            let used = (g.vram_used ?? 0) / 1e9
                            let total = (g.vram_total ?? 0) / 1e9
                            Text("\(Int(used))/\(Int(total))G")
                                .font(.system(size: 10, weight: .medium, design: .monospaced))
                        }
                        FleetProgressBar(
                            value: (g.vram_total ?? 1) > 0
                                ? (g.vram_used ?? 0) / (g.vram_total ?? 1)
                                : 0,
                            color: .blue
                        )

                        if let t = g.temp {
                            HStack {
                                Text("TEMP")
                                    .font(.system(size: 9, weight: .semibold))
                                    .foregroundStyle(.secondary)
                                Spacer()
                                Text("\(Int(t))°C")
                                    .font(.system(size: 10, weight: .medium, design: .monospaced))
                                    .foregroundStyle(t >= 75 ? .orange : .primary)
                            }
                        }
                    }
                    .padding(.leading, 16)
                } else {
                    HStack(spacing: 16) {
                        VStack(alignment: .leading, spacing: 3) {
                            Text("CPU")
                                .font(.system(size: 9, weight: .semibold))
                                .foregroundStyle(.secondary)
                            FleetProgressBar(
                                value: (n.cpu_pct ?? 0) / 100,
                                color: .green
                            )
                            Text("\(Int(n.cpu_pct ?? 0))%")
                                .font(.system(size: 10, design: .monospaced))
                        }
                        VStack(alignment: .leading, spacing: 3) {
                            Text("RAM")
                                .font(.system(size: 9, weight: .semibold))
                                .foregroundStyle(.secondary)
                            FleetProgressBar(
                                value: (n.mem_pct ?? 0) / 100,
                                color: (n.mem_pct ?? 0) >= 90 ? .orange : .blue
                            )
                            Text("\(Int(n.mem_pct ?? 0))%")
                                .font(.system(size: 10, design: .monospaced))
                        }
                    }
                    .padding(.leading, 16)
                }
            }
        }
        .padding(10)
        .background(.quaternary.opacity(0.3), in: RoundedRectangle(cornerRadius: 8))
    }

    // MARK: - Library

    private var librarySection: some View {
        VStack(alignment: .leading, spacing: 6) {
            if service.data.library.isEmpty {
                emptyState(icon: "books.vertical", message: "No library data")
            } else {
                let totalModels = service.data.library.reduce(0) { $0 + $1.models.count }
                let totalGB = service.data.library.reduce(0.0) { $0 + $1.disk_bytes } / 1e9
                HStack {
                    Text("\(totalModels) models")
                        .font(.system(size: 11, weight: .medium))
                    Spacer()
                    Text("\(Int(totalGB))G on disk")
                        .font(.system(size: 11, design: .monospaced))
                        .foregroundStyle(.secondary)
                }
                .padding(.bottom, 4)

                ForEach(service.data.library) { node in
                    libraryNodeCard(node)
                }
            }
        }
    }

    private func libraryNodeCard(_ node: LibraryNode) -> some View {
        DisclosureGroup {
            VStack(alignment: .leading, spacing: 2) {
                ForEach(node.models) { m in
                    HStack(spacing: 6) {
                        Circle()
                            .fill((m.loaded ?? false) ? Color.green : Color.secondary.opacity(0.3))
                            .frame(width: 6, height: 6)
                        Text(truncate(m.model_id, to: 34))
                            .font(.system(size: 10, design: .monospaced))
                            .foregroundStyle((m.loaded ?? false) ? .primary : .secondary)
                            .lineLimit(1)
                        Spacer()
                        Text("\(Int((m.size_bytes ?? 0) / 1e9))G")
                            .font(.system(size: 10, design: .monospaced))
                            .foregroundStyle(.secondary)
                    }
                    .padding(.vertical, 2)
                }
            }
            .padding(.top, 4)
        } label: {
            HStack {
                Text(node.label)
                    .font(.system(size: 12, weight: .semibold))
                Spacer()
                Text("\(node.models.count) models · \(Int(node.disk_bytes / 1e9))G")
                    .font(.system(size: 10, design: .monospaced))
                    .foregroundStyle(.secondary)
            }
        }
        .padding(10)
        .background(.quaternary.opacity(0.3), in: RoundedRectangle(cornerRadius: 8))
    }

    // MARK: - Settings

    private var settingsSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            VStack(alignment: .leading, spacing: 6) {
                Text("Server URL")
                    .font(.system(size: 11, weight: .semibold))
                HStack(spacing: 8) {
                    TextField("http://your-server:8090", text: $editingURL)
                        .textFieldStyle(.roundedBorder)
                        .font(.system(size: 11, design: .monospaced))
                    Button("Save") { saveServerURL() }
                        .buttonStyle(.bordered)
                        .controlSize(.small)
                }
                if showingURLSaved {
                    Text("Saved — refreshing…")
                        .font(.system(size: 10))
                        .foregroundStyle(.green)
                        .transition(.opacity)
                }
            }

            Divider()

            VStack(alignment: .leading, spacing: 8) {
                Text("Appearance")
                    .font(.system(size: 11, weight: .semibold))
                Toggle(isOn: $showInDock) {
                    VStack(alignment: .leading, spacing: 1) {
                        Text("Show in Dock")
                            .font(.system(size: 11))
                        Text("Display app icon in the Dock")
                            .font(.system(size: 9))
                            .foregroundStyle(.secondary)
                    }
                }
                .toggleStyle(.switch)
                .controlSize(.small)
                .onChange(of: showInDock) { _, show in
                    NSApp.setActivationPolicy(show ? .regular : .accessory)
                }
            }

            Divider()

            VStack(alignment: .leading, spacing: 6) {
                Text("Refresh Interval")
                    .font(.system(size: 11, weight: .semibold))
                Picker("", selection: $refreshInterval) {
                    Text("10s").tag(10.0)
                    Text("30s").tag(30.0)
                    Text("60s").tag(60.0)
                }
                .pickerStyle(.segmented)
                .onChange(of: refreshInterval) { _, interval in
                    service.updateInterval(interval)
                }
            }

            Divider()

            VStack(alignment: .leading, spacing: 6) {
                Text("Keyboard Shortcuts")
                    .font(.system(size: 11, weight: .semibold))
                shortcutRow("Refresh", shortcut: "⌘R")
                shortcutRow("Quit", shortcut: "⌘Q")
            }

            Divider()

            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text("FleetStat v1.0")
                        .font(.system(size: 11, weight: .medium))
                    Text("Monitor your self-hosted LLM fleet")
                        .font(.system(size: 10))
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Button("Quit") { NSApp.terminate(nil) }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
            }
        }
    }

    private func shortcutRow(_ label: String, shortcut: String) -> some View {
        HStack {
            Text(label)
                .font(.system(size: 10))
                .foregroundStyle(.secondary)
            Spacer()
            Text(shortcut)
                .font(.system(size: 10, design: .monospaced))
                .foregroundStyle(.tertiary)
                .padding(.horizontal, 6)
                .padding(.vertical, 2)
                .background(.quaternary, in: RoundedRectangle(cornerRadius: 4))
        }
    }

    private func saveServerURL() {
        let url = editingURL.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !url.isEmpty else { return }
        let path = NSString("~/.config/fleet-stat/server").expandingTildeInPath
        try? FileManager.default.createDirectory(
            atPath: NSString("~/.config/fleet-stat").expandingTildeInPath,
            withIntermediateDirectories: true
        )
        try? url.write(toFile: path, atomically: true, encoding: .utf8)
        service.restartEventStream()
        withAnimation { showingURLSaved = true }
        Task {
            await service.refresh()
            try? await Task.sleep(nanoseconds: 2_000_000_000)
            withAnimation { showingURLSaved = false }
        }
    }

    // MARK: - Unreachable

    private var unreachableView: some View {
        VStack(spacing: 10) {
            if let url = service.baseURL {
                Image(systemName: "wifi.slash")
                    .font(.system(size: 28))
                    .foregroundStyle(.secondary)
                Text("Fleet Stat unreachable")
                    .font(.system(size: 13, weight: .medium))
                Text(url)
                    .font(.system(size: 10, design: .monospaced))
                    .foregroundStyle(.secondary)
                Text("Check that the server is running and reachable from this Mac.")
                    .font(.system(size: 10))
                    .foregroundStyle(.secondary)
            } else {
                Image(systemName: "server.rack")
                    .font(.system(size: 28))
                    .foregroundStyle(.secondary)
                Text("Welcome to Fleet Stat")
                    .font(.system(size: 13, weight: .medium))
                Text("Add your Fleet Stat server URL in Settings to get started.")
                    .font(.system(size: 10))
                    .foregroundStyle(.secondary)
            }
            HStack(spacing: 12) {
                if service.isConfigured {
                    Button("Retry") { Task { await service.refresh() } }
                        .buttonStyle(.bordered)
                        .controlSize(.small)
                }
                Button("Settings") {
                    selectedTab = .settings
                    service.data.reachable = true
                }
                .buttonStyle(.borderless)
                .controlSize(.small)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 24)
        .padding(.horizontal, 14)
    }

    // MARK: - Footer

    private var footerView: some View {
        HStack {
            if let base = service.baseURL, let url = URL(string: base) {
                Link(destination: url) {
                    HStack(spacing: 4) {
                        Image(systemName: "arrow.up.right.square")
                            .font(.system(size: 10))
                        Text("Dashboard")
                            .font(.system(size: 11))
                    }
                }
            }
            Spacer()
            Button {
                Task { await service.refresh() }
            } label: {
                HStack(spacing: 4) {
                    Image(systemName: "arrow.clockwise")
                        .font(.system(size: 10))
                    Text("Refresh")
                        .font(.system(size: 11))
                }
            }
            .buttonStyle(.borderless)
            .keyboardShortcut("r", modifiers: .command)

            Button("") { NSApp.terminate(nil) }
                .keyboardShortcut("q", modifiers: .command)
                .frame(width: 0, height: 0)
                .opacity(0)
        }
    }

    // MARK: - Helpers

    private func statusBadge(_ text: String, color: Color) -> some View {
        Text(text)
            .font(.system(size: 9, weight: .bold, design: .monospaced))
            .foregroundStyle(color)
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(color.opacity(0.12), in: RoundedRectangle(cornerRadius: 4))
    }

    private func emptyState(icon: String, message: String) -> some View {
        VStack(spacing: 6) {
            Image(systemName: icon)
                .font(.system(size: 20))
                .foregroundStyle(.tertiary)
            Text(message)
                .font(.system(size: 11))
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 24)
    }

    private func modelColor(_ m: Model) -> Color {
        if !(m.up ?? false) { return .red }
        if (m.running ?? 0) > 0 { return .green }
        return .secondary
    }

    private func identityString(_ ident: ModelIdentity) -> String {
        var parts: [String] = []
        if let active = ident.active {
            parts.append("A\(active.hasPrefix("A") ? String(active.dropFirst()) : active)")
        } else if let params = ident.params {
            parts.append(params)
        }
        if let quant = ident.quant { parts.append(quant) }
        return parts.joined(separator: " ")
    }

    private func truncate(_ s: String, to n: Int) -> String {
        s.count <= n ? s : String(s.prefix(n - 1)) + "…"
    }

    private func fmtNum(_ n: Double?) -> String {
        guard let n else { return "—" }
        if n >= 1e6 { return String(format: "%.1fM", n / 1e6) }
        if n >= 1e3 { return "\(Int(n / 1e3))k" }
        return "\(Int(n))"
    }

    private func fmtTokens(_ n: Double) -> String {
        if n >= 1e6 { return String(format: "%.1fM", n / 1e6) }
        if n >= 1e3 { return String(format: "%.0fK", n / 1e3) }
        return "\(Int(n))"
    }

    private func timeString(_ ts: Double) -> String {
        let fmt = DateFormatter()
        fmt.dateFormat = "HH:mm"
        return fmt.string(from: Date(timeIntervalSince1970: ts))
    }

    private func agoString(_ ts: Double?) -> String {
        let s = max(0, Date().timeIntervalSince1970 - (ts ?? 0))
        if s < 60 { return "\(Int(s))s ago" }
        if s < 3600 { return "\(Int(s / 60))m ago" }
        return String(format: "%.1fh ago", s / 3600)
    }
}

// MARK: - Bar Chart

struct TokenBarChart: View {
    let days: [DayStat]

    var body: some View {
        let maxVal = days.map(\.tokens).max() ?? 1

        GeometryReader { geo in
            HStack(alignment: .bottom, spacing: 3) {
                ForEach(days) { day in
                    VStack(spacing: 3) {
                        Spacer(minLength: 0)

                        if day == days.last || day.tokens == maxVal {
                            Text(fmtTokens(day.tokens))
                                .font(.system(size: 8, weight: .medium, design: .monospaced))
                                .foregroundStyle(.secondary)
                        }

                        RoundedRectangle(cornerRadius: 2)
                            .fill(barColor(day))
                            .frame(height: max(2, (geo.size.height - 24) * day.tokens / maxVal))

                        Text(dayLabel(day.date))
                            .font(.system(size: 8, design: .monospaced))
                            .foregroundStyle(.tertiary)
                    }
                    .frame(maxWidth: .infinity)
                }
            }
        }
    }

    private func barColor(_ day: DayStat) -> Color {
        let cal = Calendar.current
        if cal.isDateInToday(day.date) { return .green }
        return .blue.opacity(0.7)
    }

    private func dayLabel(_ date: Date) -> String {
        let fmt = DateFormatter()
        fmt.dateFormat = "E"
        return String(fmt.string(from: date).prefix(2))
    }

    private func fmtTokens(_ n: Double) -> String {
        if n >= 1e6 { return String(format: "%.1fM", n / 1e6) }
        if n >= 1e3 { return String(format: "%.0fK", n / 1e3) }
        return "\(Int(n))"
    }
}

extension DayStat: Equatable {
    static func == (lhs: DayStat, rhs: DayStat) -> Bool { lhs.id == rhs.id }
}

// MARK: - Progress Bar

struct FleetProgressBar: View {
    let value: Double
    let color: Color
    var height: CGFloat = 6

    var body: some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                RoundedRectangle(cornerRadius: height / 2)
                    .fill(.quaternary)
                RoundedRectangle(cornerRadius: height / 2)
                    .fill(color)
                    .frame(width: max(0, geo.size.width * min(value, 1.0)))
            }
        }
        .frame(height: height)
    }
}

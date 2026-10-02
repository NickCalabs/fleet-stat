import Foundation

struct FleetData {
    var models: [Model] = []
    var nodes: [Node] = []
    var sessions: [Session] = []
    var sessions7d: [Session] = []
    var library: [LibraryNode] = []
    var events: [FleetEvent] = []      // warn+ in the last 24h, newest first
    var errors1h: [FleetEvent] = []    // errors in the last hour, newest first
    var reachable: Bool = false
}

struct Model: Codable, Identifiable {
    var id: String { name }
    let name: String
    let up: Bool?
    let hidden: Bool?
    let running: Int?
    let gen_tps: Double?
    let hosts: [String]?
    let identity: ModelIdentity?
}

struct ModelIdentity: Codable {
    let params: String?
    let active: String?
    let quant: String?
}

struct Node: Codable, Identifiable {
    var id: String { label }
    let label: String
    let up: Bool?
    let cpu_pct: Double?
    let mem_pct: Double?
    let gpu: GPUInfo?
}

struct GPUInfo: Codable {
    let util_pct: Double?
    let vram_used: Double?
    let vram_total: Double?
    let temp: Double?
}

struct Session: Codable, Identifiable {
    let id: String?
    let model: String?
    let title: String?
    let active: Bool?
    let ctx_tokens: Double?
    let ctx_ceiling: Double?
    let fill_pct: Double?
    let last_ts: Double?
    let first_ts: Double?
    let tokens_total: Double?
    let requests: Int?
    let utility: Bool?
    let harness: String?
    let harness_label: String?
    let failed: Bool?
    let reported: Bool?

    var stableID: String { id ?? UUID().uuidString }
    var isReal: Bool { !(utility ?? false) && (model != nil || (ctx_tokens ?? 0) > 0) }
}

struct LibraryNode: Codable, Identifiable {
    var id: String { label }
    let label: String
    let disk_bytes: Double
    let models: [LibraryModel]
}

struct LibraryModel: Codable, Identifiable {
    var id: String { model_id }
    let model_id: String
    let size_bytes: Double?
    let loaded: Bool?
}

struct SessionsResponse: Codable {
    let sessions: [Session]
}

struct FleetResponse: Codable {
    let models: [Model]
    let nodes: [Node]
}

struct LibraryResponse: Codable {
    let nodes: [LibraryNode]
}

struct FleetEvent: Codable, Identifiable {
    let id: String
    let seq: Int?
    let ts: Double
    let node: String?
    let source: String?
    let kind: String?
    let level: String
    let title: String
    let detail: String?

    var isError: Bool { level == "error" }
    var isWarnOrWorse: Bool { level == "warn" || level == "error" }
}

struct EventsResponse: Codable {
    let cursor: Int?
    let events: [FleetEvent]
}

// MARK: - Computed stats

struct DayStat: Identifiable {
    let id: String // "MM-dd"
    let date: Date
    let tokens: Double
    let requests: Int
    let sessions: Int
}

struct UsageStats {
    let totalTokens7d: Double
    let totalRequests7d: Int
    let totalSessions7d: Int
    let todayTokens: Double
    let todayRequests: Int
    let dailyStats: [DayStat]
    let byModel: [(name: String, tokens: Double, requests: Int)]
    let byHarness: [(name: String, tokens: Double, requests: Int)]
    let topModel: String?

    static let empty = UsageStats(
        totalTokens7d: 0, totalRequests7d: 0, totalSessions7d: 0,
        todayTokens: 0, todayRequests: 0,
        dailyStats: [], byModel: [], byHarness: [], topModel: nil
    )

    static func compute(from sessions: [Session]) -> UsageStats {
        let real = sessions.filter { $0.isReal }
        let cal = Calendar.current
        let todayStart = cal.startOfDay(for: Date())

        var byDay: [String: (date: Date, tokens: Double, requests: Int, sessions: Int)] = [:]
        var byModel: [String: (tokens: Double, requests: Int)] = [:]
        var byHarness: [String: (tokens: Double, requests: Int)] = [:]
        var todayTok = 0.0
        var todayReq = 0

        for s in real {
            let ts = s.first_ts ?? s.last_ts ?? 0
            let d = Date(timeIntervalSince1970: ts)
            let fmt = DateFormatter()
            fmt.dateFormat = "MM-dd"
            let key = fmt.string(from: d)
            let dayStart = cal.startOfDay(for: d)

            let tok = s.tokens_total ?? 0
            let req = s.requests ?? 0

            var entry = byDay[key] ?? (date: dayStart, tokens: 0, requests: 0, sessions: 0)
            entry.tokens += tok
            entry.requests += req
            entry.sessions += 1
            byDay[key] = entry

            if dayStart == todayStart {
                todayTok += tok
                todayReq += req
            }

            let modelName = s.model ?? "unknown"
            var me = byModel[modelName] ?? (tokens: 0, requests: 0)
            me.tokens += tok
            me.requests += req
            byModel[modelName] = me

            let harnessName = s.harness_label ?? s.harness ?? "unknown"
            var he = byHarness[harnessName] ?? (tokens: 0, requests: 0)
            he.tokens += tok
            he.requests += req
            byHarness[harnessName] = he
        }

        let dailyStats = byDay.map { DayStat(id: $0.key, date: $0.value.date, tokens: $0.value.tokens, requests: $0.value.requests, sessions: $0.value.sessions) }
            .sorted { $0.date < $1.date }

        let sortedModels = byModel.map { (name: $0.key, tokens: $0.value.tokens, requests: $0.value.requests) }
            .sorted { $0.tokens > $1.tokens }

        let sortedHarnesses = byHarness.map { (name: $0.key, tokens: $0.value.tokens, requests: $0.value.requests) }
            .sorted { $0.tokens > $1.tokens }

        return UsageStats(
            totalTokens7d: real.reduce(0) { $0 + ($1.tokens_total ?? 0) },
            totalRequests7d: real.reduce(0) { $0 + ($1.requests ?? 0) },
            totalSessions7d: real.count,
            todayTokens: todayTok,
            todayRequests: todayReq,
            dailyStats: dailyStats,
            byModel: sortedModels,
            byHarness: sortedHarnesses,
            topModel: sortedModels.first?.name
        )
    }
}

@MainActor
class FleetService: ObservableObject {
    @Published var data = FleetData()
    @Published var stats = UsageStats.empty

    private var timer: Timer?
    private let urlSession = URLSession(configuration: {
        let c = URLSessionConfiguration.default
        c.timeoutIntervalForRequest = 5
        c.timeoutIntervalForResource = 10
        return c
    }())

    // Event stream connections stay open, so they get their own session without the short resource timeout.
    private let streamSession = URLSession(configuration: {
        let c = URLSessionConfiguration.default
        c.timeoutIntervalForRequest = 120
        c.timeoutIntervalForResource = 7 * 24 * 3600
        return c
    }())
    private var streamTask: Task<Void, Never>?
    private var lastSeq: Int?

    static let eventLimit = 8

    /// Server URL from ~/.config/fleet-stat/server, or nil if not set yet.
    var baseURL: String? {
        guard let url = try? String(contentsOfFile: NSString("~/.config/fleet-stat/server").expandingTildeInPath, encoding: .utf8) else { return nil }
        let trimmed = url.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty { return nil }
        return trimmed.hasSuffix("/") ? String(trimmed.dropLast()) : trimmed
    }

    var isConfigured: Bool { baseURL != nil }

    var errorsLastHour: [FleetEvent] {
        let cutoff = Date().timeIntervalSince1970 - 3600
        return data.errors1h.filter { $0.ts >= cutoff }
    }

    private var interval: TimeInterval = 30

    func start() {
        Task { await refresh() }
        scheduleTimer()
    }

    func stop() {
        timer?.invalidate()
        timer = nil
    }

    func updateInterval(_ newInterval: TimeInterval) {
        interval = newInterval
        timer?.invalidate()
        scheduleTimer()
    }

    private func scheduleTimer() {
        timer = Timer.scheduledTimer(withTimeInterval: interval, repeats: true) { [weak self] _ in
            guard let self else { return }
            Task { @MainActor in await self.refresh() }
        }
    }

    func refresh() async {
        guard isConfigured else {
            data.reachable = false
            return
        }
        do {
            async let fleetReq = fetch(FleetResponse.self, path: "/api/fleet")
            async let sessionsReq = fetch(SessionsResponse.self, path: "/api/sessions?hours=6")
            async let sessions7dReq = fetch(SessionsResponse.self, path: "/api/sessions?hours=168")

            let fleet = try await fleetReq
            let sessions = try await sessionsReq
            let sessions7d = try await sessions7dReq

            var lib: [LibraryNode] = []
            if let libResp = try? await fetch(LibraryResponse.self, path: "/api/library") {
                lib = libResp.nodes
            }

            // Events are optional: older servers don't have them.
            async let eventsReq = fetch(EventsResponse.self, path: "/api/events?hours=24&level=warn&limit=\(Self.eventLimit)")
            async let errorsReq = fetch(EventsResponse.self, path: "/api/events?hours=1&level=error&limit=200")
            let events = try? await eventsReq
            let errors = try? await errorsReq
            if let cursor = events?.cursor ?? errors?.cursor {
                lastSeq = max(lastSeq ?? cursor, cursor)
            }

            data = FleetData(
                models: fleet.models.filter { !($0.hidden ?? false) },
                nodes: fleet.nodes,
                sessions: sessions.sessions,
                sessions7d: sessions7d.sessions,
                library: lib,
                events: events?.events ?? data.events,
                errors1h: errors?.events ?? data.errors1h,
                reachable: true
            )
            stats = UsageStats.compute(from: sessions7d.sessions)
            if lastSeq != nil { startEventStream() }
        } catch {
            data.reachable = false
        }
    }

    // MARK: - Live events (server-sent events)

    /// Call after the server URL changes so the stream reconnects to the new server.
    func restartEventStream() {
        streamTask?.cancel()
        streamTask = nil
        lastSeq = nil
        data.events = []
        data.errors1h = []
    }

    private func startEventStream() {
        guard streamTask == nil else { return }
        streamTask = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                await self.consumeEventStream()
                try? await Task.sleep(nanoseconds: 5_000_000_000)
            }
        }
    }

    private func consumeEventStream() async {
        guard let base = baseURL, let after = lastSeq,
              let url = URL(string: base + "/api/events/stream?after=\(after)") else { return }
        var request = URLRequest(url: url)
        request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        request.setValue("FleetStat-macOS", forHTTPHeaderField: "User-Agent")
        do {
            let (bytes, response) = try await streamSession.bytes(for: request)
            guard (response as? HTTPURLResponse)?.statusCode == 200 else { return }
            // One `data:` line per event; `.lines` drops the blank separator lines.
            for try await line in bytes.lines {
                if line.hasPrefix("id:"), let id = Int(line.dropFirst(3).trimmingCharacters(in: .whitespaces)) {
                    lastSeq = max(lastSeq ?? id, id)
                } else if line.hasPrefix("data:") {
                    let json = line.dropFirst(5).trimmingCharacters(in: .whitespaces)
                    if let event = try? JSONDecoder().decode(FleetEvent.self, from: Data(json.utf8)) {
                        ingest(event)
                    }
                }
            }
        } catch {
            // Dropped connection or cancellation; the loop reconnects from lastSeq.
        }
    }

    private func ingest(_ event: FleetEvent) {
        if let seq = event.seq { lastSeq = max(lastSeq ?? seq, seq) }
        guard event.isWarnOrWorse, !data.events.contains(where: { $0.id == event.id }) else { return }
        data.events = Array((data.events + [event]).sorted { $0.ts > $1.ts }.prefix(Self.eventLimit))
        if event.isError {
            data.errors1h = (data.errors1h + [event]).sorted { $0.ts > $1.ts }
        }
    }

    private func fetch<T: Decodable>(_ type: T.Type, path: String) async throws -> T {
        guard let base = baseURL, let url = URL(string: base + path) else { throw URLError(.badURL) }
        var request = URLRequest(url: url)
        request.setValue("FleetStat-macOS", forHTTPHeaderField: "User-Agent")
        let (responseData, _) = try await urlSession.data(for: request)
        return try JSONDecoder().decode(T.self, from: responseData)
    }
}

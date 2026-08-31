import Foundation

struct FleetData {
    var models: [Model] = []
    var nodes: [Node] = []
    var sessions: [Session] = []
    var sessions7d: [Session] = []
    var library: [LibraryNode] = []
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

    var baseURL: String {
        if let url = try? String(contentsOfFile: NSString("~/.config/fleet-stat/server").expandingTildeInPath, encoding: .utf8) {
            let trimmed = url.trimmingCharacters(in: .whitespacesAndNewlines)
            if !trimmed.isEmpty { return trimmed.hasSuffix("/") ? String(trimmed.dropLast()) : trimmed }
        }
        return "http://localhost:8090"
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

            data = FleetData(
                models: fleet.models.filter { !($0.hidden ?? false) },
                nodes: fleet.nodes,
                sessions: sessions.sessions,
                sessions7d: sessions7d.sessions,
                library: lib,
                reachable: true
            )
            stats = UsageStats.compute(from: sessions7d.sessions)
        } catch {
            data.reachable = false
        }
    }

    private func fetch<T: Decodable>(_ type: T.Type, path: String) async throws -> T {
        let url = URL(string: baseURL + path)!
        var request = URLRequest(url: url)
        request.setValue("FleetStat-macOS", forHTTPHeaderField: "User-Agent")
        let (responseData, _) = try await urlSession.data(for: request)
        return try JSONDecoder().decode(T.self, from: responseData)
    }
}

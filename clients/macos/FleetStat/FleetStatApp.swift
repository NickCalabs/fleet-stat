import SwiftUI

@main
struct FleetStatApp: App {
    @StateObject private var service = FleetService()

    var body: some Scene {
        MenuBarExtra {
            MenuBarView(service: service)
                .onAppear { service.start() }
        } label: {
            MenuBarIcon(service: service)
        }
        .menuBarExtraStyle(.window)
    }

    init() {
        NSApplication.shared.setActivationPolicy(.accessory)
    }
}

struct MenuBarIcon: View {
    @ObservedObject var service: FleetService
    @State private var pulse = false

    private var models: [Model] { service.data.models }
    private var up: [Model] { models.filter { $0.up ?? false } }
    private var tps: Double { up.reduce(0.0) { $0 + ($1.gen_tps ?? 0) } }
    private var isGenerating: Bool { tps >= 1 }
    private var isDown: Bool { up.count < models.count && !models.isEmpty }
    private var hasRecentErrors: Bool { !service.errorsLastHour.isEmpty }

    var body: some View {
        HStack(spacing: 4) {
            fleetIcon
            statusText
        }
    }

    private var fleetIcon: some View {
        ZStack {
            // Outer hexagon
            Image(systemName: isGenerating ? "hexagon.fill" : "hexagon")
                .font(.system(size: 14, weight: .medium))
                .foregroundStyle(iconColor)
                .opacity(isGenerating && pulse ? 0.7 : 1.0)

            // Inner dot grid — represents the fleet nodes
            if service.data.reachable && !models.isEmpty {
                HStack(spacing: 1.5) {
                    ForEach(0..<min(models.count, 4), id: \.self) { i in
                        Circle()
                            .fill(i < up.count ? dotColor : Color.red)
                            .frame(width: 2, height: 2)
                    }
                }
                .offset(y: isGenerating ? 0 : 0)
            }
        }
        .animation(.easeInOut(duration: 1.0).repeatForever(autoreverses: true), value: pulse)
        .onChange(of: isGenerating) { _, generating in
            pulse = generating
        }
    }

    private var statusText: some View {
        Group {
            if !service.data.reachable {
                Text("⋯")
                    .foregroundStyle(.secondary)
            } else if isGenerating {
                Text("\(Int(tps))t/s")
                    .foregroundStyle(.green)
            } else if isDown {
                Text("\(up.count)/\(models.count)")
                    .foregroundStyle(.red)
            } else {
                Text("\(up.count)/\(models.count)")
                    .foregroundStyle(.primary)
            }
        }
        .font(.system(size: 11, weight: .medium, design: .monospaced))
    }

    private var iconColor: Color {
        if !service.data.reachable { return .secondary }
        if isDown || hasRecentErrors { return .red }
        if isGenerating { return .green }
        return .primary
    }

    private var dotColor: Color {
        isGenerating ? .white : .primary
    }
}

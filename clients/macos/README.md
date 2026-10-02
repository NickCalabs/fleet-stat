# Fleet Stat for macOS

A menu bar app for your Fleet Stat server: models up and live tokens/sec in the
menu bar, plus a window with usage, sessions, nodes, the model library and
recent events. The icon turns red when the server logged an error in the last
hour.

## Build and run

Requires macOS 15.1+ and Xcode 16+.

1. Open `FleetStat.xcodeproj`.
2. Under Signing & Capabilities, pick your own team (or "Sign to Run Locally").
3. Press ⌘R.

On first launch, open the menu bar window, go to **Settings**, and enter your
server URL (for example `http://your-server:8090`). The app is sandboxed, so the
URL is stored in its container at
`~/Library/Containers/com.nickcalabs.FleetStat/Data/.config/fleet-stat/server`.

## What it uses

`/api/fleet`, `/api/sessions`, `/api/library`, `/api/events`, and
`/api/events/stream` for live events. See "API for clients" in the top-level
README.

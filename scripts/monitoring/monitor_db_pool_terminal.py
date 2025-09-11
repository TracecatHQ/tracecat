#!/usr/bin/env python3
# type: ignore
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "plotext>=5.3.2",
#     "requests>=2.31.0",
# ]
# ///
"""
Database Pool Monitor with Terminal Graphs
Real-time terminal visualization using plotext.
"""

import os
import signal
import sys
import time
from collections import deque
from datetime import datetime

import plotext as plt
import requests


class DBPoolTerminalMonitor:
    def __init__(
        self,
        url: str = "http://localhost:8001/health/db-pool",
        interval: float = 0.5,
        max_points: int = 120,
    ):  # 60 seconds of data at 0.5s interval
        self.url = url
        self.interval = interval
        self.max_points = max_points
        self.running = True

        # Use deques for efficient sliding window
        self.timestamps: deque[float] = deque(maxlen=max_points)
        self.pool_size: deque[int] = deque(maxlen=max_points)
        self.checked_out: deque[int] = deque(maxlen=max_points)
        self.overflow: deque[int] = deque(maxlen=max_points)
        self.utilization: deque[float] = deque(maxlen=max_points)

        # Statistics
        self.total_samples = 0
        self.start_time = datetime.now()
        self.max_checked_out = 0
        self.max_utilization = 0.0
        self.first_draw = True

        # Register signal handlers
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)

    def signal_handler(self, signum, frame):
        _ = signum, frame  # Unused but required by signal handler
        """Handle shutdown signals gracefully."""
        self.running = False
        # Exit alternate screen buffer
        sys.stdout.write("\033[?1049l")
        sys.stdout.flush()
        print("\n\nMonitoring stopped.")
        self.print_final_statistics()
        sys.exit(0)

    def collect_data(self) -> dict | None:
        """Collect data from the endpoint."""
        try:
            response = requests.get(self.url, timeout=1)
            response.raise_for_status()
            return response.json()
        except Exception:
            return None

    def update_data(self, data: dict):
        """Update the data buffers with new sample."""
        if not data:
            return

        timestamp = time.time() - self.start_time.timestamp()
        self.timestamps.append(timestamp)

        pool = data.get("pool_size", 0)
        checked = data.get("checked_out", 0)
        overflow = data.get("overflow", 0)

        self.pool_size.append(pool)
        self.checked_out.append(checked)
        self.overflow.append(abs(overflow))

        # Calculate utilization
        util = (checked / pool * 100) if pool > 0 else 0
        self.utilization.append(util)

        # Update statistics
        self.total_samples += 1
        self.max_checked_out = max(self.max_checked_out, checked)
        self.max_utilization = max(self.max_utilization, util)

    def draw_graphs(self):
        """Draw real-time graphs in the terminal."""
        # Use alternate screen buffer and cursor positioning to prevent flashing
        if self.first_draw:
            # Enter alternate screen buffer (like vim/less)
            sys.stdout.write("\033[?1049h")
            sys.stdout.flush()
            self.first_draw = False

        # Move cursor to home position instead of clearing
        sys.stdout.write("\033[H")
        sys.stdout.flush()

        # Configure plotext
        plt.theme("dark")
        plt.subplots(2, 2)
        plt.plotsize(100, 30)

        # Convert timestamps to seconds elapsed
        if self.timestamps:
            x_data = [t - self.timestamps[0] for t in self.timestamps]
        else:
            x_data = []

        # Plot 1: Connections
        plt.subplot(1, 1)
        plt.clear_data()
        plt.title("Database Pool Connections")
        if x_data:
            plt.plot(x_data, list(self.pool_size), label="Pool Size", color="blue")
            plt.plot(x_data, list(self.checked_out), label="Checked Out", color="red")
            plt.plot(x_data, list(self.overflow), label="|Overflow|", color="green")
        plt.xlabel("Time (seconds)")
        plt.ylabel("Connections")

        # Plot 2: Utilization
        plt.subplot(1, 2)
        plt.clear_data()
        plt.title("Pool Utilization %")
        if x_data:
            plt.plot(x_data, list(self.utilization), color="magenta")
            # Use braille dots for better visibility without flashing
            if len(x_data) > 1:
                plt.scatter(
                    x_data[::2],
                    list(self.utilization)[::2],
                    color="magenta",
                    marker="braille",
                )
        plt.xlabel("Time (seconds)")
        plt.ylabel("Utilization %")
        plt.ylim(0, 100)

        # Plot 3: Current Status (bar chart)
        plt.subplot(2, 1)
        plt.clear_data()
        plt.title("Current Status")
        if self.pool_size:
            categories = ["Pool", "Checked", "Available", "Overflow"]
            current_pool = self.pool_size[-1]
            current_checked = self.checked_out[-1]
            current_available = current_pool - current_checked
            current_overflow = self.overflow[-1]
            values = [
                current_pool,
                current_checked,
                current_available,
                current_overflow,
            ]
            plt.bar(categories, values)
        plt.ylabel("Count")

        # Plot 4: Statistics
        plt.subplot(2, 2)
        plt.clear_data()
        plt.title("Live Statistics")

        # Show plots without clearing terminal
        plt.show()

        # Display statistics below graphs
        stats_text = self.get_stats_text()
        print(stats_text)

    def get_stats_text(self) -> str:
        """Generate statistics text."""
        elapsed = (datetime.now() - self.start_time).total_seconds()

        if not self.pool_size:
            return "Waiting for data..."

        current_pool = self.pool_size[-1]
        current_checked = self.checked_out[-1]
        current_util = self.utilization[-1]

        avg_checked = sum(self.checked_out) / len(self.checked_out)
        avg_util = sum(self.utilization) / len(self.utilization)

        # Use ANSI codes to create a fixed-width statistics box
        stats = f"""
\033[K╔══════════════════════════════════════╗
\033[K║         LIVE STATISTICS              ║
\033[K╠══════════════════════════════════════╣
\033[K║ Runtime:        {elapsed:>8.1f} seconds  ║
\033[K║ Total Samples:  {self.total_samples:>8}          ║
\033[K║                                      ║
\033[K║ Current Pool:   {current_pool:>8}          ║
\033[K║ Current Used:   {current_checked:>8}          ║
\033[K║ Current Util:   {current_util:>7.1f}%          ║
\033[K║                                      ║
\033[K║ Avg Checked:    {avg_checked:>8.1f}          ║
\033[K║ Avg Util:       {avg_util:>7.1f}%          ║
\033[K║ Max Checked:    {self.max_checked_out:>8}          ║
\033[K║ Max Util:       {self.max_utilization:>7.1f}%          ║
\033[K╚══════════════════════════════════════╝
\033[K Press Ctrl+C to stop and exit"""
        return stats

    def print_final_statistics(self):
        """Print final statistics on exit."""
        if not self.pool_size:
            print("No data collected.")
            return

        elapsed = (datetime.now() - self.start_time).total_seconds()
        avg_checked = sum(self.checked_out) / len(self.checked_out)
        avg_util = sum(self.utilization) / len(self.utilization)

        print("\n" + "=" * 50)
        print("FINAL MONITORING REPORT")
        print("=" * 50)
        print(f"Total Runtime:     {elapsed:.1f} seconds")
        print(f"Total Samples:     {self.total_samples}")
        print(f"Sample Rate:       {self.total_samples / elapsed:.1f} samples/sec")
        print("\nConnection Statistics:")
        print(f"  Max Checked Out: {self.max_checked_out}")
        print(f"  Avg Checked Out: {avg_checked:.2f}")
        print(f"  Max Utilization: {self.max_utilization:.2f}%")
        print(f"  Avg Utilization: {avg_util:.2f}%")
        print("=" * 50)

    def run(self):
        """Main monitoring loop."""
        print("Starting DB Pool Terminal Monitor")
        print(f"URL: {self.url}")
        print(f"Interval: {self.interval}s")
        print("Press Ctrl+C to stop\n")
        time.sleep(1)  # Give user time to read before switching screens

        while self.running:
            data = self.collect_data()
            if data:
                self.update_data(data)
                self.draw_graphs()

            time.sleep(self.interval)


if __name__ == "__main__":
    # Set terminal to handle UTF-8 properly
    if os.name != "nt":  # Unix/Linux/Mac
        os.environ["PYTHONIOENCODING"] = "utf-8"

    monitor = DBPoolTerminalMonitor()
    monitor.run()

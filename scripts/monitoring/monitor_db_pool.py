#!/usr/bin/env python3
# type: ignore
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "matplotlib>=3.10.6",
#     "requests>=2.31.0",
# ]
# ///
"""
Database Pool Monitor
Polls the database pool health endpoint and generates graphs on exit.
"""

import signal
import time
from collections import defaultdict
from datetime import datetime

import matplotlib.pyplot as plt
import requests
from matplotlib.dates import DateFormatter


class DBPoolMonitor:
    def __init__(
        self, url: str = "http://localhost:8001/health/db-pool", interval: float = 0.5
    ):
        self.url = url
        self.interval = interval
        self.data: dict[str, list] = defaultdict(list)
        self.timestamps: list[datetime] = []
        self.running = True

        # Register signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)

    def signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully."""
        _ = signum, frame  # Unused but required by signal handler interface
        print("\n\nShutting down and generating graphs...")
        self.running = False

    def collect_data(self):
        """Collect data from the endpoint."""
        try:
            response = requests.get(self.url, timeout=1)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"Error fetching data: {e}")
            return None

    def run(self):
        """Main monitoring loop."""
        print("Starting DB Pool Monitor")
        print(f"Polling {self.url} every {self.interval} seconds")
        print("Press Ctrl+C to stop and generate graphs\n")

        start_time = datetime.now()
        sample_count = 0

        while self.running:
            data = self.collect_data()
            if data:
                timestamp = datetime.now()
                self.timestamps.append(timestamp)

                # Store each metric
                self.data["pool_size"].append(data.get("pool_size", 0))
                self.data["checked_out"].append(data.get("checked_out", 0))
                self.data["checked_in"].append(data.get("checked_in", 0))
                self.data["overflow"].append(data.get("overflow", 0))
                self.data["healthy"].append(1 if data.get("healthy", False) else 0)

                sample_count += 1

                # Print status every 10 samples (5 seconds)
                if sample_count % 10 == 0:
                    elapsed = (timestamp - start_time).total_seconds()
                    print(
                        f"[{timestamp.strftime('%H:%M:%S')}] "
                        f"Samples: {sample_count}, "
                        f"Elapsed: {elapsed:.1f}s, "
                        f"Pool: {data.get('pool_size')}, "
                        f"Checked out: {data.get('checked_out')}, "
                        f"Overflow: {data.get('overflow')}"
                    )

            time.sleep(self.interval)

        self.generate_graphs()

    def generate_graphs(self):
        """Generate graphs from collected data."""
        if not self.timestamps:
            print("No data collected. Exiting.")
            return

        print(f"\nCollected {len(self.timestamps)} samples")
        print("Generating graphs...")

        # Create figure with subplots
        fig, axes = plt.subplots(3, 2, figsize=(15, 12))
        fig.suptitle(
            f"Database Pool Monitoring Report\n"
            f"{self.timestamps[0].strftime('%Y-%m-%d %H:%M:%S')} to "
            f"{self.timestamps[-1].strftime('%Y-%m-%d %H:%M:%S')}",
            fontsize=14,
            fontweight="bold",
        )

        # Plot 1: Pool Size
        ax1 = axes[0, 0]
        ax1.plot(self.timestamps, self.data["pool_size"], "b-", linewidth=2)
        ax1.set_title("Pool Size Over Time")
        ax1.set_ylabel("Pool Size")
        ax1.grid(True, alpha=0.3)
        ax1.xaxis.set_major_formatter(DateFormatter("%H:%M:%S"))

        # Plot 2: Checked Out Connections
        ax2 = axes[0, 1]
        ax2.plot(self.timestamps, self.data["checked_out"], "r-", linewidth=2)
        ax2.fill_between(
            self.timestamps, self.data["checked_out"], alpha=0.3, color="red"
        )
        ax2.set_title("Checked Out Connections")
        ax2.set_ylabel("Connections")
        ax2.grid(True, alpha=0.3)
        ax2.xaxis.set_major_formatter(DateFormatter("%H:%M:%S"))

        # Plot 3: Overflow
        ax3 = axes[1, 0]
        ax3.plot(self.timestamps, self.data["overflow"], "g-", linewidth=2)
        ax3.set_title("Connection Overflow")
        ax3.set_ylabel("Overflow")
        ax3.grid(True, alpha=0.3)
        ax3.xaxis.set_major_formatter(DateFormatter("%H:%M:%S"))

        # Plot 4: Combined View
        ax4 = axes[1, 1]
        ax4.plot(
            self.timestamps,
            self.data["pool_size"],
            "b-",
            label="Pool Size",
            linewidth=2,
        )
        ax4.plot(
            self.timestamps,
            self.data["checked_out"],
            "r-",
            label="Checked Out",
            linewidth=2,
        )
        ax4.plot(
            self.timestamps,
            [abs(o) for o in self.data["overflow"]],
            "g-",
            label="|Overflow|",
            linewidth=2,
        )
        ax4.set_title("Combined Metrics")
        ax4.set_ylabel("Count")
        ax4.legend()
        ax4.grid(True, alpha=0.3)
        ax4.xaxis.set_major_formatter(DateFormatter("%H:%M:%S"))

        # Plot 5: Connection Utilization (%)
        ax5 = axes[2, 0]
        utilization = []
        for i in range(len(self.timestamps)):
            pool = self.data["pool_size"][i]
            checked = self.data["checked_out"][i]
            util = (checked / pool * 100) if pool > 0 else 0
            utilization.append(util)
        ax5.plot(self.timestamps, utilization, "m-", linewidth=2)
        ax5.fill_between(self.timestamps, utilization, alpha=0.3, color="magenta")
        ax5.set_title("Pool Utilization (%)")
        ax5.set_ylabel("Utilization %")
        ax5.set_xlabel("Time")
        ax5.grid(True, alpha=0.3)
        ax5.xaxis.set_major_formatter(DateFormatter("%H:%M:%S"))

        # Plot 6: Health Status
        ax6 = axes[2, 1]
        ax6.plot(
            self.timestamps,
            self.data["healthy"],
            "g-",
            linewidth=2,
            marker="o",
            markersize=2,
        )
        ax6.fill_between(
            self.timestamps, self.data["healthy"], alpha=0.3, color="green"
        )
        ax6.set_title("Health Status")
        ax6.set_ylabel("Healthy (1=Yes, 0=No)")
        ax6.set_xlabel("Time")
        ax6.set_ylim(-0.1, 1.1)
        ax6.grid(True, alpha=0.3)
        ax6.xaxis.set_major_formatter(DateFormatter("%H:%M:%S"))

        # Rotate x-axis labels for better readability
        for ax in axes.flat:
            plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha="right")

        plt.tight_layout()

        # Save the plot
        filename = f"db_pool_monitor_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        plt.savefig(filename, dpi=100, bbox_inches="tight")
        print(f"Graph saved as: {filename}")

        # Show the plot
        plt.show()

        # Print summary statistics
        self.print_statistics()

    def print_statistics(self):
        """Print summary statistics of the collected data."""
        print("\n" + "=" * 50)
        print("SUMMARY STATISTICS")
        print("=" * 50)

        for metric in ["pool_size", "checked_out", "overflow"]:
            if metric in self.data and self.data[metric]:
                values = self.data[metric]
                print(f"\n{metric.replace('_', ' ').title()}:")
                print(f"  Min: {min(values)}")
                print(f"  Max: {max(values)}")
                print(f"  Avg: {sum(values) / len(values):.2f}")

        # Calculate utilization stats
        utilization = []
        for i in range(len(self.timestamps)):
            pool = self.data["pool_size"][i]
            checked = self.data["checked_out"][i]
            if pool > 0:
                utilization.append(checked / pool * 100)

        if utilization:
            print("\nPool Utilization (%):")
            print(f"  Min: {min(utilization):.2f}%")
            print(f"  Max: {max(utilization):.2f}%")
            print(f"  Avg: {sum(utilization) / len(utilization):.2f}%")

        # Health status
        if "healthy" in self.data and self.data["healthy"]:
            health_rate = sum(self.data["healthy"]) / len(self.data["healthy"]) * 100
            print(f"\nHealth Rate: {health_rate:.2f}%")

        print("\n" + "=" * 50)


if __name__ == "__main__":
    monitor = DBPoolMonitor()
    monitor.run()

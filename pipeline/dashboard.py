import os
import sys
import time
import json
import urllib.request
import urllib.error
from datetime import datetime

try:
    from rich.live import Live
    from rich.table import Table
    from rich.panel import Panel
    from rich.console import Console
    from rich.layout import Layout
    from rich.align import Align
    from rich import box
except ImportError:
    print("Error: The 'rich' library is required to run the dashboard.")
    print("Please install it using: pip install rich")
    sys.exit(1)

# Configuration
STORE_ID = "STORE_1076"
METRICS_URL = f"http://localhost:8000/stores/{STORE_ID}/metrics"
ANOMALIES_URL = f"http://localhost:8000/stores/{STORE_ID}/anomalies"

def fetch_json(url):
    """
    Fetch and decode JSON from the target HTTP endpoint.
    Returns (data, success_boolean)
    """
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Store-Intelligence-Dashboard/1.0"}
    )
    try:
        with urllib.request.urlopen(req, timeout=2.0) as res:
            return json.loads(res.read().decode("utf-8")), True
    except Exception:
        return None, False

def create_dashboard_layout(metrics, anomalies, connection_status, last_updated):
    """
    Builds the rich layout showing performing metrics and alert panels.
    """
    # 1. Header Area
    status_color = "green" if "Connected" in connection_status else "red"
    header_text = f"[bold magenta]Purplle Store Intelligence Live Console[/bold magenta] | Store ID: [cyan]{STORE_ID}[/cyan] | API Status: [bold {status_color}]{connection_status}[/bold {status_color}]"
    header_panel = Panel(Align.center(header_text), box=box.ROUNDED)

    # 2. Performance Metrics Panel
    table = Table(box=box.ROUNDED, expand=True)
    table.add_column("Shopper Metric", style="cyan", no_wrap=True)
    table.add_column("Value", style="bold green", justify="right")

    if metrics and "Connected" in connection_status:
        visitors = metrics.get("unique_visitors", 0)
        conv = metrics.get("conversion_rate", 0.0) * 100.0
        q_depth = metrics.get("queue_depth", 0)
        abandon = metrics.get("abandonment_rate", 0.0) * 100.0

        table.add_row("Unique Visitors (Traffic)", f"{visitors} sessions")
        table.add_row("Conversion Rate", f"{round(conv, 2)}%")
        table.add_row("Current Queue Depth", f"{q_depth} people")
        table.add_row("Queue Abandonment Rate", f"{round(abandon, 2)}%")
    else:
        table.add_row("Unique Visitors (Traffic)", "API Offline...")
        table.add_row("Conversion Rate", "API Offline...")
        table.add_row("Current Queue Depth", "API Offline...")
        table.add_row("Queue Abandonment Rate", "API Offline...")

    metrics_panel = Panel(table, title="[bold]Store Performance Metrics[/bold]", box=box.ROUNDED)

    # 3. Active Anomalies Panel
    anoms_content = ""
    if "Offline" in connection_status:
        anoms_content = "[yellow]Waiting for API server connection to recover...[/yellow]"
    elif anomalies and anomalies.get("anomalies"):
        active_list = anomalies["anomalies"]
        for idx, a in enumerate(active_list, 1):
            severity = a.get("severity", "INFO").upper()
            
            # Color-code alert severities
            color = "blue"
            if severity == "WARN":
                color = "yellow"
            elif severity == "CRITICAL":
                color = "red"

            anoms_content += f"[bold {color}][{severity}][/bold {color}] [bold]{a.get('anomaly_type')}[/bold]: {a.get('description')}\n"
            anoms_content += f"          [dim]Action: {a.get('suggested_action')}[/dim]\n\n"
    else:
        anoms_content = "[green]✔ No active system or traffic anomalies detected.[/green]"

    anomalies_panel = Panel(anoms_content.strip(), title="[bold]Active Alerts & Anomalies[/bold]", box=box.ROUNDED)

    # 4. Footer Area
    footer_text = f"[dim]Last Updated: {last_updated} | Polling: Metrics (3s), Anomalies (5s) | Press Ctrl+C to exit[/dim]"
    footer_panel = Panel(Align.center(footer_text), box=box.MINIMAL)

    # Assemble Split Layout
    layout = Layout()
    layout.split_column(
        Layout(header_panel, name="header", size=3),
        Layout(name="body"),
        Layout(footer_panel, name="footer", size=3)
    )
    layout["body"].split_row(
        Layout(metrics_panel, name="metrics"),
        Layout(anomalies_panel, name="anomalies")
    )
    return layout

def main():
    console = Console()
    console.print("[bold yellow]Starting Purplle Store Intelligence Console...[/bold yellow]")

    metrics_data = None
    anomalies_data = None
    
    last_metrics_poll = 0.0
    last_anomalies_poll = 0.0
    
    connection_status = "Connecting..."
    last_updated = "Never"

    # Initialize rich Live view
    dashboard_layout = create_dashboard_layout(metrics_data, anomalies_data, connection_status, last_updated)
    
    try:
        with Live(dashboard_layout, refresh_per_second=2, screen=True) as live:
            while True:
                now_time = time.time()
                trigger_update = False

                # 1. Poll Metrics every 3 seconds
                if now_time - last_metrics_poll >= 3.0:
                    data, ok = fetch_json(METRICS_URL)
                    last_metrics_poll = now_time
                    if ok:
                        metrics_data = data
                        connection_status = "Connected"
                    else:
                        connection_status = "Offline (Reconnecting...)"
                    trigger_update = True

                # 2. Poll Anomalies every 5 seconds
                if now_time - last_anomalies_poll >= 5.0:
                    data, ok = fetch_json(ANOMALIES_URL)
                    last_anomalies_poll = now_time
                    if ok:
                        anomalies_data = data
                        connection_status = "Connected"
                    else:
                        connection_status = "Offline (Reconnecting...)"
                    trigger_update = True

                # 3. Update the UI every loop tick (captures last_updated timestamps cleanly)
                last_updated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
                # Re-render layout
                updated_layout = create_dashboard_layout(
                    metrics_data, 
                    anomalies_data, 
                    connection_status, 
                    last_updated
                )
                live.update(updated_layout)
                
                # Sleep briefly to enable responsive Ctrl+C interrupts
                time.sleep(0.5)

    except KeyboardInterrupt:
        console.print("\n[bold yellow]Dashboard connection terminated by user.[/bold yellow]")

if __name__ == "__main__":
    main()

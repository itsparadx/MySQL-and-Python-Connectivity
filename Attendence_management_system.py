"""
Attendance Management System: the cool edition.

Setup:
    pip install mysql-connector-python rich

Optional environment variables (defaults shown):
    DB_HOST=localhost  DB_USER=root  DB_PASSWORD=  DB_NAME=attendance_db
"""
import csv
import os
from datetime import date, datetime

import mysql.connector
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, IntPrompt
from rich.table import Table
from rich.text import Text

console = Console()

STATUSES = ("Present", "Absent", "Late")
STATUS_STYLE = {"Present": "bold green", "Absent": "bold red", "Late": "bold yellow"}
STATUS_ICON = {"Present": "✔", "Absent": "✘", "Late": "◷"}


# ---------- helpers ----------
def styled_status(status):
    return Text(f"{STATUS_ICON.get(status, '?')} {status}", style=STATUS_STYLE.get(status, ""))


def ask_status(default="Present"):
    choice = Prompt.ask(
        "[cyan]Status[/cyan]",
        choices=[s.lower() for s in STATUSES],
        default=default.lower(),
    )
    return choice.capitalize()


def ask_date(label, default=None):
    """Prompt until a valid YYYY-MM-DD date is entered."""
    default = default or date.today().isoformat()
    while True:
        raw = Prompt.ask(f"[cyan]{label}[/cyan] (YYYY-MM-DD)", default=default)
        try:
            return datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            console.print("[red]That's not a valid date, try again.[/red]")


def bar(pct, width=20):
    filled = round(pct / 100 * width)
    color = "green" if pct >= 75 else "yellow" if pct >= 50 else "red"
    return Text("█" * filled, style=color) + Text("░" * (width - filled), style="grey37")


def attendance_table(rows, title, show_student=True):
    table = Table(title=title, box=box.ROUNDED, header_style="bold magenta", title_style="bold cyan")
    table.add_column("ID", justify="right", style="dim")
    if show_student:
        table.add_column("Student", justify="right", style="cyan")
    table.add_column("Date")
    table.add_column("Status")
    for rec_id, student_id, day, status in rows:
        cells = [str(rec_id)]
        if show_student:
            cells.append(str(student_id))
        cells += [str(day), styled_status(status)]
        table.add_row(*cells)
    return table


# ---------- database ----------
def connect_to_database():
    cfg = {
        "host": os.getenv("DB_HOST", "localhost"),
        "user": os.getenv("DB_USER", "root"),
        "password": os.getenv("DB_PASSWORD", ""),
    }
    db_name = os.getenv("DB_NAME", "attendance_db")
    try:
        with console.status("[cyan]Connecting to MySQL...[/cyan]", spinner="dots"):
            connection = mysql.connector.connect(**cfg)
            cursor = connection.cursor()
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{db_name}`")
            cursor.execute(f"USE `{db_name}`")
            cursor.close()
        console.print(f"[green]✔ Connected[/green] to [bold]{db_name}[/bold]")
        return connection
    except mysql.connector.Error as err:
        console.print(f"[red]Connection failed:[/red] {err}")
        return None


def create_attendance_table(connection):
    cursor = connection.cursor()
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS attendance (
                id INT AUTO_INCREMENT PRIMARY KEY,
                student_id INT NOT NULL,
                date DATE NOT NULL,
                status VARCHAR(10) NOT NULL,
                UNIQUE KEY uq_student_day (student_id, date)
            )
        """)
    except mysql.connector.Error as err:
        console.print(f"[red]Error:[/red] {err}")
    finally:
        cursor.close()


# ---------- actions ----------
def mark_attendance(connection):
    student_id = IntPrompt.ask("[cyan]Student ID[/cyan]")
    day = ask_date("Date")
    status = ask_status()
    cursor = connection.cursor()
    try:
        # One record per student per day: re-marking just updates it.
        cursor.execute("""
            INSERT INTO attendance (student_id, date, status)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE status = VALUES(status)
        """, (student_id, day, status))
        connection.commit()
        console.print(Panel(
            Text.assemble(f"Student {student_id} on {day}: ", styled_status(status)),
            title="Saved", border_style="green", expand=False,
        ))
    except mysql.connector.Error as err:
        connection.rollback()
        console.print(f"[red]Error:[/red] {err}")
    finally:
        cursor.close()


def update_attendance(connection):
    student_id = IntPrompt.ask("[cyan]Student ID[/cyan]")
    day = ask_date("Date")
    new_status = ask_status()
    cursor = connection.cursor()
    try:
        cursor.execute("""
            UPDATE attendance SET status = %s
            WHERE student_id = %s AND date = %s
        """, (new_status, student_id, day))
        connection.commit()
        if cursor.rowcount:
            console.print(f"[green]✔ Updated[/green] student {student_id} on {day} → ", styled_status(new_status))
        else:
            console.print("[yellow]No matching record found (or the status was already that value).[/yellow]")
    except mysql.connector.Error as err:
        connection.rollback()
        console.print(f"[red]Error:[/red] {err}")
    finally:
        cursor.close()


def view_attendance(connection):
    student_id = IntPrompt.ask("[cyan]Student ID[/cyan]")
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT id, student_id, date, status FROM attendance WHERE student_id = %s ORDER BY date DESC",
            (student_id,),
        )
        rows = cursor.fetchall()
        if not rows:
            console.print(f"[yellow]No records for student {student_id}.[/yellow]")
            return
        console.print(attendance_table(rows, f"Student {student_id}", show_student=False))

        # Mini stats card: Late counts as attended
        attended = sum(1 for r in rows if r[3] in ("Present", "Late"))
        pct = attended / len(rows) * 100
        stats = Text.assemble(f"{attended}/{len(rows)} days attended  ", bar(pct), f"  {pct:.0f}%")
        console.print(Panel(stats, title="Attendance rate", border_style="cyan", expand=False))
    except mysql.connector.Error as err:
        console.print(f"[red]Error:[/red] {err}")
    finally:
        cursor.close()


def view_attendance_report(connection):
    console.print("[dim]Leave a filter blank to skip it.[/dim]")
    sid_raw = Prompt.ask("[cyan]Student ID[/cyan]", default="")
    start_raw = Prompt.ask("[cyan]Start date[/cyan] (YYYY-MM-DD)", default="")
    end_raw = Prompt.ask("[cyan]End date[/cyan] (YYYY-MM-DD)", default="")

    where, params = [], []
    try:
        if sid_raw:
            where.append("student_id = %s")
            params.append(int(sid_raw))
        if start_raw:
            where.append("date >= %s")
            params.append(datetime.strptime(start_raw, "%Y-%m-%d").date())
        if end_raw:
            where.append("date <= %s")
            params.append(datetime.strptime(end_raw, "%Y-%m-%d").date())
    except ValueError:
        console.print("[red]Invalid student ID or date format.[/red]")
        return

    clause = f" WHERE {' AND '.join(where)}" if where else ""
    cursor = connection.cursor()
    try:
        cursor.execute(f"SELECT id, student_id, date, status FROM attendance{clause} ORDER BY date DESC, student_id", params)
        rows = cursor.fetchall()
        if not rows:
            console.print("[yellow]No records match those filters.[/yellow]")
            return
        console.print(attendance_table(rows, f"Attendance Report ({len(rows)} records)"))
    except mysql.connector.Error as err:
        console.print(f"[red]Error:[/red] {err}")
    finally:
        cursor.close()


def leaderboard(connection):
    cursor = connection.cursor()
    try:
        cursor.execute("""
            SELECT student_id,
                   COUNT(*) AS total,
                   SUM(status IN ('Present', 'Late')) AS attended
            FROM attendance
            GROUP BY student_id
            ORDER BY attended / COUNT(*) DESC, total DESC
        """)
        rows = cursor.fetchall()
        if not rows:
            console.print("[yellow]No data yet.[/yellow]")
            return
        table = Table(title="🏆 Attendance Leaderboard", box=box.ROUNDED,
                      header_style="bold magenta", title_style="bold cyan")
        table.add_column("#", justify="right", style="dim")
        table.add_column("Student", justify="right", style="cyan")
        table.add_column("Days", justify="right")
        table.add_column("Rate")
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        for i, (sid, total, attended) in enumerate(rows, 1):
            pct = int(attended) / total * 100
            table.add_row(medals.get(i, str(i)), str(sid), f"{int(attended)}/{total}",
                          Text.assemble(bar(pct, 15), f" {pct:.0f}%"))
        console.print(table)
    except mysql.connector.Error as err:
        console.print(f"[red]Error:[/red] {err}")
    finally:
        cursor.close()


def export_to_csv(connection):
    filename = Prompt.ask("[cyan]CSV file name[/cyan]", default="attendance.csv")
    if not filename.lower().endswith(".csv"):
        filename += ".csv"
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT id, student_id, date, status FROM attendance ORDER BY date, student_id")
        rows = cursor.fetchall()
        if not rows:
            console.print("[yellow]Nothing to export.[/yellow]")
            return
        with open(filename, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["ID", "Student ID", "Date", "Status"])
            writer.writerows(rows)
        console.print(f"[green]✔ Exported {len(rows)} records to[/green] [bold]{filename}[/bold]")
    except (mysql.connector.Error, OSError) as err:
        console.print(f"[red]Error:[/red] {err}")
    finally:
        cursor.close()


# ---------- main ----------
MENU = [
    ("1", "Mark attendance", mark_attendance),
    ("2", "Update attendance", update_attendance),
    ("3", "View a student", view_attendance),
    ("4", "Filtered report", view_attendance_report),
    ("5", "Leaderboard", leaderboard),
    ("6", "Export to CSV", export_to_csv),
    ("0", "Exit", None),
]


def show_menu():
    table = Table.grid(padding=(0, 2))
    for key, label, _ in MENU:
        table.add_row(f"[bold cyan]{key}[/bold cyan]", label)
    console.print(Panel(table, title="📋 Attendance Manager", subtitle=date.today().strftime("%A, %d %B %Y"),
                        border_style="magenta", expand=False, padding=(1, 3)))


def main():
    connection = connect_to_database()
    if not connection:
        return
    create_attendance_table(connection)

    actions = {key: fn for key, _, fn in MENU}
    try:
        while True:
            show_menu()
            choice = Prompt.ask("[bold]Choose[/bold]", choices=list(actions), show_choices=False)
            if choice == "0":
                break
            console.print()
            actions[choice](connection)
            console.print()
    except (KeyboardInterrupt, EOFError):
        console.print()
    finally:
        connection.close()
        console.print("[dim]Connection closed. See you! 👋[/dim]")


if __name__ == "__main__":
    main()

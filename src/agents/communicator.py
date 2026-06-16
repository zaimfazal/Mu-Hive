import logging
import re
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.columns import Columns
from src.agents.planner import plan_digests
from src.config.logging_config import setup_logging

logger = logging.getLogger(__name__)
console = Console()

# --- Emoji icons for each category ---
CATEGORY_ICONS = {
    "News": "📰",
    "Hackathons": "💻",
}

# --- Emoji icons for each Interest Group ---
IG_ICONS = {
    "AI": "🤖",
    "Data Science": "📊",
    "Web Development": "🌐",
    "Cyber Security": "🔒",
    "UI/UX": "🎨",
}


def strip_html(text: str) -> str:
    """Remove any HTML tags from text."""
    if not text:
        return ""
    return re.sub(r'<[^>]+>', '', text).strip()


def truncate_url(url: str, max_len: int = 80) -> str:
    """Truncate a URL for display, keeping the domain visible."""
    if not url or len(url) <= max_len:
        return url
    return url[:max_len - 3] + "..."


def parse_hackathon_summary(summary: str) -> dict:
    """
    Parse hackathon structured summary into a dict of fields.
    The summary is stored as 'Key: Value' lines separated by newlines.
    """
    fields = {}
    if not summary:
        return fields
    for line in summary.split('\n'):
        line = line.strip()
        if ':' in line:
            key, _, value = line.partition(':')
            key = key.strip()
            value = strip_html(value.strip())
            if key and value:
                fields[key] = value
    return fields


def format_news_details(opp: dict) -> str:
    """Format a news/general item for display."""
    title = strip_html(opp.get('title', 'No Title'))
    link = opp.get('link', '')
    summary = strip_html(opp.get('summary', ''))
    
    # Truncate summary cleanly at word boundary
    if len(summary) > 200:
        summary = summary[:197].rsplit(' ', 1)[0] + "..."
    summary = summary.replace('\\n', '\n')
    
    display_url = truncate_url(link)
    source = opp.get('source_engine', '')
    source_badge = f" [dim]via {source}[/dim]" if source else ""
    
    details = f"[bold]{title}[/bold]{source_badge}\n"
    details += f"[blue][link={link}]{display_url}[/link][/blue]\n"
    if summary:
        details += f"[dim]{summary}[/dim]"
    return details


def format_hackathon_details(opp: dict) -> str:
    """Format a hackathon/event with structured metadata fields."""
    title = strip_html(opp.get('title', 'No Title'))
    link = opp.get('link', '')
    summary = opp.get('summary', '')
    
    display_url = truncate_url(link)
    details = f"[bold]{title}[/bold]\n"
    details += f"[blue][link={link}]{display_url}[/link][/blue]\n"
    
    # Parse structured fields from summary or use the provided metadata
    fields = opp.get('structured_metadata') or parse_hackathon_summary(summary)
    
    if fields:
        if fields.get("Platform"):
            details += f"[dim]🏢 Platform:[/dim] {fields['Platform']}\n"
        if fields.get("Start"):
            start = fields.get("Start", "TBA")
            end = fields.get("End", "")
            if end and end != start:
                details += f"[dim]📅 Dates:[/dim] {start} → {end}\n"
            else:
                details += f"[dim]📅 Start:[/dim] {start}\n"
        if fields.get("Location"):
            details += f"[dim]📍 Location:[/dim] {fields['Location']}\n"
        if fields.get("Prize Pool") and fields["Prize Pool"] not in ("", "TBA"):
            details += f"[dim]🏆 Prize:[/dim] {fields['Prize Pool']}\n"
        if fields.get("Cost"):
            details += f"[dim]💰 Cost:[/dim] {fields['Cost']}\n"
        if fields.get("Eligibility"):
            details += f"[dim]🎯 Eligible:[/dim] {fields['Eligibility']}\n"
        if fields.get("Tags"):
            details += f"[dim]🏷️  Tags:[/dim] {fields['Tags']}"
    else:
        # Fallback: display raw summary truncated
        if summary:
            clean = strip_html(summary)
            if len(clean) > 200:
                clean = clean[:197].rsplit(' ', 1)[0] + "..."
            details += f"[dim]{clean}[/dim]"
    
    return details


def format_internship_details(opp: dict) -> str:
    """Format an internship with available metadata."""
    title = strip_html(opp.get('title', 'No Title'))
    link = opp.get('link', '')
    summary = opp.get('summary', '')
    
    display_url = truncate_url(link)
    details = f"[bold]{title}[/bold]\n"
    details += f"[blue][link={link}]{display_url}[/link][/blue]\n"
    
    # Try to parse structured fields from summary
    fields = parse_hackathon_summary(summary)
    
    if fields:
        if fields.get("Platform"):
            details += f"[dim]🏢 Platform:[/dim] {fields['Platform']}\n"
        if fields.get("Company"):
            details += f"[dim]🏛️  Company:[/dim] {fields['Company']}\n"
        if fields.get("Start") or fields.get("Deadline"):
            date_val = fields.get("Start") or fields.get("Deadline", "TBA")
            details += f"[dim]📅 Date:[/dim] {date_val}\n"
        if fields.get("Location"):
            details += f"[dim]📍 Location:[/dim] {fields['Location']}\n"
        if fields.get("Duration"):
            details += f"[dim]⏱️  Duration:[/dim] {fields['Duration']}\n"
        if fields.get("Stipend"):
            details += f"[dim]💰 Stipend:[/dim] {fields['Stipend']}\n"
        if fields.get("Eligibility"):
            details += f"[dim]🎯 Eligible:[/dim] {fields['Eligibility']}\n"
        if fields.get("Tags"):
            details += f"[dim]🏷️  Tags:[/dim] {fields['Tags']}"
    else:
        # Fallback: display raw summary truncated
        if summary:
            clean = strip_html(summary)
            if len(clean) > 200:
                clean = clean[:197].rsplit(' ', 1)[0] + "..."
            details += f"[dim]{clean}[/dim]"
    
    return details


def format_digest(ig, opportunities_by_cat):
    """Prints a curated digest block for terminal display using Rich."""
    ig_icon = IG_ICONS.get(ig, "📌")
    
    console.print(Panel.fit(
        f"[bold white]{ig_icon}  {ig.upper()}  —  Intelligence Digest[/bold white]",
        border_style="cyan",
        padding=(0, 2),
    ))
    
    # Categories that get structured hackathon formatting
    structured_categories = {"hackathons"}
    internship_categories = {"internships"}
    
    for cat_name, opps in opportunities_by_cat.items():
        if not opps: continue
        
        cat_icon = CATEGORY_ICONS.get(cat_name, "📌")
        table = Table(
            title=f"[bold magenta]{cat_icon} {cat_name.upper()}[/bold magenta]",
            show_lines=True,
            title_style="bold magenta",
            border_style="dim",
            pad_edge=True,
        )
        table.add_column("Score", style="yellow", justify="center", width=7)
        table.add_column("Details", style="white", min_width=60)

        for opp in opps:
            if cat_name.lower() in structured_categories:
                details = format_hackathon_details(opp)
            elif cat_name.lower() in internship_categories:
                details = format_internship_details(opp)
            else:
                details = format_news_details(opp)
            
            score_display = f"{opp['score']}/10"
            table.add_row(score_display, details)
            
        console.print(table)
    
    console.print()  # Spacer between IGs


def run_communicator():
    """Curates and displays the top-scored opportunities per Interest Group.
    Returns a stats dict for phase reporting."""
    logger.info("Initializing Communicator Agent...")
    digests = plan_digests()

    stats = {
        "igs_with_content": 0,
        "total_items_displayed": 0,
        "by_ig": {},
        "by_category": {},
    }

    if not digests:
        logger.warning("No opportunities passed quality threshold (score >= 6). Nothing to display.")
        return stats

    console.print()
    console.print(Panel.fit(
        "[bold green]✨  MU-HIVE INTELLIGENCE DIGEST  ✨[/bold green]",
        border_style="green",
        padding=(1, 3),
    ))
    console.print()

    for ig, opportunities_by_cat in digests.items():
        format_digest(ig, opportunities_by_cat)
        total_items = sum(len(cat_list) for cat_list in opportunities_by_cat.values())
        logger.info(f"Displayed {total_items} curated items for IG: {ig}")

        # Track stats
        stats["igs_with_content"] += 1
        stats["total_items_displayed"] += total_items
        stats["by_ig"][ig] = total_items
        for cat_name, cat_list in opportunities_by_cat.items():
            stats["by_category"][cat_name] = stats["by_category"].get(cat_name, 0) + len(cat_list)

    console.print(Panel.fit(
        "[bold green]✅  Pipeline complete. All digests displayed above.[/bold green]",
        border_style="green",
    ))

    return stats

if __name__ == "__main__":
    setup_logging()
    run_communicator()

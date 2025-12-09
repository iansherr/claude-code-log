"""HTML renderer implementation for Claude Code transcripts."""

from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from ..cache import get_library_version
from ..models import TranscriptEntry
from ..renderer import (
    Renderer,
    check_html_version,
    generate_html,
    prepare_projects_index,
    title_for_projects_index,
)
from .utils import get_template_environment

if TYPE_CHECKING:
    from ..cache import CacheManager


class HtmlRenderer(Renderer):
    """HTML renderer for Claude Code transcripts."""

    def generate(
        self,
        messages: List[TranscriptEntry],
        title: Optional[str] = None,
        combined_transcript_link: Optional[str] = None,
    ) -> str:
        """Generate HTML from transcript messages."""
        return generate_html(messages, title, combined_transcript_link)

    def generate_session(
        self,
        messages: List[TranscriptEntry],
        session_id: str,
        title: Optional[str] = None,
        cache_manager: Optional["CacheManager"] = None,
    ) -> str:
        """Generate HTML for a single session."""
        # Filter messages for this session (SummaryTranscriptEntry.sessionId is always None)
        session_messages = [msg for msg in messages if msg.sessionId == session_id]

        # Get combined transcript link if cache manager is available
        combined_link = None
        if cache_manager is not None:
            try:
                project_cache = cache_manager.get_cached_project_data()
                if project_cache and project_cache.sessions:
                    combined_link = "combined_transcripts.html"
            except Exception:
                pass

        return self.generate(
            session_messages,
            title or f"Session {session_id[:8]}",
            combined_transcript_link=combined_link,
        )

    def generate_projects_index(
        self,
        project_summaries: List[Dict[str, Any]],
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> str:
        """Generate an HTML projects index page."""
        title = title_for_projects_index(project_summaries, from_date, to_date)
        template_projects, template_summary = prepare_projects_index(project_summaries)

        env = get_template_environment()
        template = env.get_template("index.html")
        return str(
            template.render(
                title=title,
                projects=template_projects,
                summary=template_summary,
                library_version=get_library_version(),
            )
        )

    def is_outdated(self, file_path: Path) -> bool:
        """Check if an HTML file is outdated based on version.

        Returns:
            True if the file should be regenerated (missing version,
            different version, or file doesn't exist).
            False if the file is current.
        """
        html_version = check_html_version(file_path)
        current_version = get_library_version()
        # If no version found or different version, it's outdated
        return html_version != current_version


# -- Convenience Functions ----------------------------------------------------


def generate_session_html(
    messages: List[TranscriptEntry],
    session_id: str,
    title: Optional[str] = None,
    cache_manager: Optional["CacheManager"] = None,
) -> str:
    """Generate HTML for a single session using Jinja2 templates."""
    return HtmlRenderer().generate_session(messages, session_id, title, cache_manager)


def generate_projects_index_html(
    project_summaries: List[Dict[str, Any]],
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> str:
    """Generate an index HTML page listing all projects using Jinja2 templates.

    This is a convenience function that delegates to HtmlRenderer.generate_projects_index.
    """
    return HtmlRenderer().generate_projects_index(project_summaries, from_date, to_date)

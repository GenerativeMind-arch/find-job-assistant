"""Best-effort MySQL archival for workflow session state."""

from log import get_logger
from storage.mysql_client import MySQLStore, get_mysql_pool
from workflow.state import CopilotState

logger = get_logger("storage")


async def _persist_to_mysql(state: CopilotState) -> None:
    """将关键状态异步持久化到 MySQL。"""
    pool = await get_mysql_pool()
    db = MySQLStore(pool)
    await db.upsert_session(state.session_id)

    if state.job:
        await db.save_job(state.job.id, state.session_id, state.job.model_dump(), state.job.version)

    if state.candidate_profile:
        profile_id = f"profile_{state.session_id}"
        await db.save_candidate_profile(profile_id, state.session_id, state.candidate_profile.model_dump())

    if state.resume_content_json:
        content_id = f"content_{state.session_id}"
        await db.save_resume_content(
            content_id, state.session_id,
            state.resume_content_json.model_dump(),
            state.resume_content_json.meta.version,
            state.resume_content_json.meta.content_hash,
        )

    render_id = f"render_{state.session_id}"
    await db.save_render_config(render_id, state.session_id,
                                state.render_config.model_dump(), state.render_config.version)

    if state.resume_html.html:
        html_id = f"html_{state.session_id}"
        await db.save_resume_html(
            html_id, state.session_id, state.resume_html.html,
            state.resume_html.version,
            state.resume_html.derived_from_content_version,
            state.resume_html.derived_from_render_version,
            state.resume_html.checksum,
        )

    if state.interview_qa:
        interview_id = f"interview_{state.session_id}"
        await db.save_interview_qa(
            interview_id, state.session_id,
            {"interview_qa": [qa.model_dump() for qa in state.interview_qa]},
            len(state.interview_qa),
        )


async def archive_session(state: CopilotState) -> None:
    try:
        await _persist_to_mysql(state)
    except Exception as e:
        logger.error("MySQL persistence failed: %s", e, exc_info=True)

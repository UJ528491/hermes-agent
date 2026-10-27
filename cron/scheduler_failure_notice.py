"""Optional Korean incident copy; raw evidence remains in the execution ledger."""
from __future__ import annotations

import logging
import sqlite3
from copy import deepcopy
from datetime import datetime

from hermes_time import get_timezone

_CAUSES = {
    "script_timeout": "작업 스크립트가 실행 제한 시간을 넘겨 중단됐습니다.",
    "script_error": "작업 스크립트가 오류로 종료됐습니다. 원인은 아직 확인되지 않았습니다.",
    "inactivity": "작업의 활동이 오래 멈춰 실행이 중단됐습니다.",
    "timeout": "모델 서비스 응답을 제한 시간 안에 받지 못했습니다.",
    "rate_limit": "모델 서비스의 요청 한도에 걸렸습니다.",
    "upstream_rate_limit": "상위 모델 서비스의 요청 한도에 걸렸습니다.",
    "overloaded": "모델 서비스가 혼잡해 요청을 처리하지 못했습니다.",
    "server_error": "모델 서비스에서 서버 오류가 발생했습니다.",
    "billing": "모델 서비스의 사용량 또는 결제 한도에 걸렸습니다.",
    "billing_unverified": "모델 서비스의 사용량 또는 결제 상태 확인이 필요합니다.",
    "auth": "모델 서비스 인증이 거부됐습니다.",
    "auth_permanent": "모델 서비스 인증이 거부됐습니다.",
    "model_not_found": "설정된 모델을 사용할 수 없습니다.",
    "context_overflow": "모델이 처리할 수 있는 입력 길이를 넘었습니다.",
    "payload_too_large": "모델 서비스가 허용하는 입력 크기를 넘었습니다.",
    "upstream_blocked": "모델 서비스 앞의 방화벽 또는 접근 차단으로 요청이 거부됐습니다.",
    "content_policy_blocked": "모델 서비스의 안전 정책에 따라 요청이 거부됐습니다.",
    "provider_policy_blocked": "모델 서비스의 계정 정책에 따라 요청이 거부됐습니다.",
}


def localized_failure_notice(job: dict, error: str | None, incident_id: str | None,
                             *, blocked_config: bool = False, agent_declared: bool = False) -> str | None:
    from cron import scheduler
    from cron.scheduler_failure_copy import classify_cron_failure
    from cron.quota_hold import hold_active, planned_hold_until
    from cron.jobs import _advance_after_run, is_terminal_job

    try:
        language = (scheduler.load_config().get("cron") or {}).get("failure_notice_language", "en")
    except Exception:
        # Configuration I/O must not lose the original failure notification.
        return None
    if language != "ko":
        return None

    incident = {}
    if incident_id:
        from cron.incidents import get_incident
        try:
            incident = get_incident(incident_id) or {}
        except (OSError, sqlite3.Error):
            logging.getLogger(__name__).debug("Failure notice incident unavailable", exc_info=True)
    reminder = bool(incident.get("alerted_at"))
    heading = "예약 작업 실패 · 재알림" if reminder else "예약 작업 실패"
    lines = [f'⚠️ [{heading}] {job.get("name") or job["id"]}']
    for key, label in (("first_seen_at", "최초 발생"), ("alerted_at", "이전 알림")):
        if incident.get(key):
            when = datetime.fromisoformat(incident[key]).astimezone(get_timezone())
            lines.append(f"{label}: {when:%Y-%m-%d %H:%M %Z}")
    if reminder:
        lines.append("같은 실패 사건의 재알림입니다. 새 오류 발생이나 실제 재시도 횟수를 뜻하지 않습니다.")
    text = (error or "").strip()
    if agent_declared:
        cause = text
    elif blocked_config:
        cause = "실행 전 설정 검사에서 작업을 시작하지 못했습니다. " + text.removeprefix("[blocked_config]").strip()
    else:
        cause = _CAUSES.get(classify_cron_failure(job, text), "원인은 아직 확인되지 않았습니다.")
    held = hold_active(job)
    pending_hold = None
    if not held and job.get("_quota_hold_seconds"):
        # Delivery precedes mark_job_run. Preview its real retirement/schedule rules on a copy.
        preview = deepcopy(job)
        _advance_after_run(preview, scheduler._hermes_now().isoformat())
        if not is_terminal_job(preview):
            pending_hold = planned_hold_until(preview, job["_quota_hold_seconds"])
    state = "자동 조치 결과와 최종 작업 결과는 이 알림에서 확인되지 않았습니다."
    action = "실패 기록을 확인해 원인을 수정한 뒤 작업별 재개 방법을 확인해야 합니다."
    if held:
        state = "모델 사용 한도가 풀린 뒤의 예약 시각까지 실행을 보류했습니다."
        action = "실행 보류가 풀릴 때까지 기다린 뒤 실행 결과를 확인하세요."
    elif pending_hold:
        when = datetime.fromisoformat(pending_hold).astimezone(get_timezone())
        state = f"한도 해제 후 {when:%Y-%m-%d %H:%M %Z}까지 다음 실행 보류 예정입니다."
        action = "실행 종료 후 반영되는 다음 예약 시각과 실행 결과를 확인하세요."
    lines.extend(["확인된 상황: " + cause, "현재 상태: " + state, "사용자 조치: " + action,
                  f'상세 기록: `hermes cron runs {job["id"]}`'])
    if blocked_config:
        lines.extend(["같은 설정 문제는 한 번만 알리며 다음 예약 시각에 설정을 다시 확인합니다.",
                      "설정 점검: `hermes cron doctor`"])
    return "\n".join(lines)

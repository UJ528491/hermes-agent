"""Korean notices describe incidents without confusing checks with retries."""
from datetime import timedelta

import pytest

import cron.scheduler as scheduler
from cron.incidents import get_incident, set_incident_state


def test_korean_incident_reminder_keeps_original_failure_identity(tmp_path, monkeypatch):
    from hermes_cli.config import load_config
    (tmp_path / "config.yaml").write_text(
        "cron:\n  failure_notice_language: ko\n  failure_repeat_alert_hours: 6\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(scheduler, "load_config", load_config)
    job = {"id": "daily", "name": "Gbrain 데일리", "no_agent": True,
           "schedule": {"kind": "cron"}, "failure_streak": 174}
    error = "Script exited with code 1\nstderr:\nbriefing must start with the morning briefing heading"
    first, *_, incident_id = scheduler._compose_run_delivery(
        job, success=False, error=error, final_response="", output_file=None)
    assert "예약 작업 실패" in first
    assert "원인은 아직 확인되지 않았습니다" in first
    assert "hermes cron run " not in first
    assert "174" not in first and "175" not in first
    set_incident_state(incident_id, "alerted")
    original = get_incident(incident_id)
    quiet, *_ = scheduler._compose_run_delivery(
        job, success=False, error=error, final_response="", output_file=None)
    assert quiet == ""
    monkeypatch.setattr(scheduler, "_hermes_now", lambda: scheduler.datetime.fromisoformat(original["alerted_at"]) + timedelta(hours=7))
    reminder, *_, repeated_id = scheduler._compose_run_delivery(
        job, success=False, error=error, final_response="", output_file=None)
    assert "재알림" in reminder and "새 오류" in reminder
    assert "최초 발생" in reminder and "이전 알림" in reminder
    assert repeated_id == incident_id
    assert "재시도 횟수" in reminder


def test_korean_script_notice_does_not_blame_a_provider_or_recommend_blind_retry(tmp_path, monkeypatch):
    from hermes_cli.config import load_config
    (tmp_path / "config.yaml").write_text("cron:\n  failure_notice_language: ko\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(scheduler, "load_config", load_config)
    job = {"id": "watch", "name": "복구 감시", "no_agent": True}
    text, *_ = scheduler._compose_run_delivery(
        job, success=False, error="Script exited with code 1: HTTP 401 inside script",
        final_response="", output_file=None)
    assert "스크립트" in text
    assert "인증" not in text
    assert "hermes cron run " not in text
    assert "자동 조치 결과" in text
    assert "hermes cron runs watch" in text


@pytest.mark.parametrize("job,error,blocked,expected,absent", [
    ({}, "[blocked_config] provider credential missing: no key", True,
     "provider credential missing", "원인은 아직 확인되지"),
    ({}, "HTTP 413: payload too large", False, "입력 크기", "원인은 아직 확인되지"),
    ({"no_agent": True}, "output persistence failed", False, "원인은 아직 확인되지", "스크립트가 오류로 종료"),
    ({"schedule": {"kind": "once"}, "_quota_hold_seconds": 60}, "HTTP 429", False,
     "예약 작업 실패", "실행을 보류했습니다"),
])
def test_korean_copy_preserves_known_evidence_without_inventing_execution_state(
        tmp_path, monkeypatch, job, error, blocked, expected, absent):
    monkeypatch.setattr(scheduler, "load_config", lambda: {"cron": {"failure_notice_language": "ko"}})
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    text, actual_blocked, *_ = scheduler._compose_run_delivery(
        {"id": "copy", "name": "예약 작업", **job}, success=False, error=error,
        final_response="", output_file=None)
    assert actual_blocked == blocked
    assert expected in text
    assert absent not in text
    if blocked:
        assert "hermes cron doctor" in text
        assert "같은 설정 문제" in text


def test_first_recurring_quota_notice_explains_the_pending_hold(tmp_path, monkeypatch):
    from cron.jobs import create_job, get_job, mark_job_run
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(scheduler, "load_config", lambda: {"cron": {"failure_notice_language": "ko"}})
    job = create_job("fixture", "every 30m", deliver="local")
    job["_quota_hold_seconds"] = 3600
    text, *_ = scheduler._compose_run_delivery(
        job, success=False, error="HTTP 429: usage limit", final_response="", output_file=None)
    assert "보류 예정" in text
    assert "원인을 수정" not in text
    assert "quota_hold_until" not in get_job(job["id"])
    mark_job_run(job["id"], False, "HTTP 429: usage limit", quota_hold_seconds=3600)
    assert get_job(job["id"])["quota_hold_until"]

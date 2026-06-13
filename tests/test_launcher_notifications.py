import applypilot.apply.launcher as launcher


class FakeChromeProc:
    def poll(self):
        return None


def _job():
    return {
        "url": "https://jobs.example/acme",
        "title": "Software Engineer",
        "company": "Acme",
        "site": "workday",
    }


def _patch_worker_loop(monkeypatch, result):
    job = _job()
    marks = []
    notifications = []

    monkeypatch.setattr(launcher, "update_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(launcher, "add_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(launcher, "launch_chrome", lambda *args, **kwargs: (FakeChromeProc(), 9222))
    monkeypatch.setattr(launcher, "cleanup_worker", lambda *args, **kwargs: None)
    monkeypatch.setattr(launcher.chrome, "detach_worker", lambda *args, **kwargs: None)
    monkeypatch.setattr(launcher, "run_job", lambda *args, **kwargs: (result, 1234))
    monkeypatch.setattr(launcher, "acquire_job", lambda *args, **kwargs: job)
    monkeypatch.setattr(launcher, "release_lock", lambda *args, **kwargs: None)
    monkeypatch.setattr(launcher, "notify", lambda event, reason, **kwargs: notifications.append((event, reason, kwargs)))

    def fake_mark_result(*args, **kwargs):
        marks.append((args, kwargs))

    monkeypatch.setattr(launcher, "mark_result", fake_mark_result)
    launcher._stop_event.clear()
    return marks, notifications


def test_worker_loop_notifies_run_failed(monkeypatch):
    marks, notifications = _patch_worker_loop(monkeypatch, "failed:form_error")

    assert launcher.worker_loop(worker_id=2, limit=1) == (0, 1)

    assert marks[0][0][:3] == ("https://jobs.example/acme", "failed", "form_error")
    assert notifications == [
        ("run_failed", "Software Engineer at Acme failed: form_error", {"worker_id": 2}),
    ]


def test_worker_loop_notifies_applied_run_finished(monkeypatch):
    marks, notifications = _patch_worker_loop(monkeypatch, "applied")

    assert launcher.worker_loop(worker_id=4, limit=1) == (1, 0)

    assert marks[0][0][:2] == ("https://jobs.example/acme", "applied")
    assert notifications == [
        ("run_finished", "Applied to Acme", {"worker_id": 4}),
    ]


def test_worker_loop_notifies_handoff_when_no_recent_needs_human(monkeypatch):
    marks, notifications = _patch_worker_loop(monkeypatch, "handoff")
    monkeypatch.setattr(launcher, "needs_human_sent_recently", lambda worker_id, *, within: False)

    assert launcher.worker_loop(worker_id=5, limit=1) == (0, 0)

    assert marks[0][0][:2] == ("https://jobs.example/acme", "handoff")
    assert notifications == [
        ("run_finished", "Acme is filled and waiting for your review", {"worker_id": 5}),
    ]


def test_worker_loop_skips_handoff_notification_after_recent_needs_human(monkeypatch):
    _, notifications = _patch_worker_loop(monkeypatch, "handoff")
    monkeypatch.setattr(launcher, "needs_human_sent_recently", lambda worker_id, *, within: True)

    assert launcher.worker_loop(worker_id=6, limit=1) == (0, 0)

    assert notifications == []


def test_batch_done_notification_only_for_multi_job_summary(monkeypatch):
    notifications = []
    monkeypatch.setattr(launcher, "notify", lambda event, reason, **kwargs: notifications.append((event, reason, kwargs)))

    launcher._notify_batch_done(1, 0)
    assert notifications == []

    launcher._notify_batch_done(1, 1)
    assert notifications == [
        ("batch_done", "Batch done: 1 applied, 1 failed", {}),
    ]

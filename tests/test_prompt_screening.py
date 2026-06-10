"""F3: screening answers come from the profile, never hardcoded."""
from applypilot.apply.prompt import _build_profile_summary, _build_screening_section


def _profile(screening=None):
    p = {
        "personal": {"full_name": "Jane Doe", "email": "j@example.com",
                     "phone": "5551234567", "city": "Austin"},
        "work_authorization": {"legally_authorized_to_work": "Yes"},
        "compensation": {"salary_expectation": "100000"},
        "experience": {"years_of_experience_total": 5},
        "availability": {},
        "eeo": {},
        "skills_boundary": {},
    }
    if screening is not None:
        p["screening"] = screening
    return p


def test_felony_yes_when_profile_says_so():
    summary = _build_profile_summary(_profile({"felony_conviction": True}))
    assert "Felony: Yes" in summary


def test_missing_screening_marks_not_provided():
    summary = _build_profile_summary(_profile())  # no screening section
    assert "NOT PROVIDED" in summary
    assert "Felony: No" not in summary
    assert "Felony: NOT PROVIDED" in summary


def test_previously_worked_here_not_hardcoded():
    summary = _build_profile_summary(_profile({"age_18_plus": True}))
    assert "Previously Worked Here: No" not in summary


def test_how_heard_omitted_when_absent():
    summary = _build_profile_summary(_profile({"age_18_plus": True}))
    assert "How Heard" not in summary


def test_screening_section_drops_old_overclaim_instruction():
    section = _build_screening_section(_profile())
    assert "Don't sell short" not in section
    # The honest replacement still allows confident YES for listed tools.
    assert "answer YES confidently" in section
    assert "needs_human_answer" in section


# ── Salary modes (APPLYPILOT_SALARY_MODE) ─────────────────────────────────

def _salary(monkeypatch, mode=None, fixed=None):
    from applypilot.apply.prompt import _build_salary_section
    if mode is None:
        monkeypatch.delenv("APPLYPILOT_SALARY_MODE", raising=False)
    else:
        monkeypatch.setenv("APPLYPILOT_SALARY_MODE", mode)
    if fixed is None:
        monkeypatch.delenv("APPLYPILOT_SALARY_FIXED", raising=False)
    else:
        monkeypatch.setenv("APPLYPILOT_SALARY_FIXED", fixed)
    return _build_salary_section(_profile())


def test_salary_default_mirrors_posting(monkeypatch):
    s = _salary(monkeypatch)
    assert "mirror the posting" in s
    assert "MIDPOINT" in s
    assert "$100000" in s  # profile floor only as the no-info fallback


def test_salary_blank_mode(monkeypatch):
    s = _salary(monkeypatch, mode="blank")
    assert "BLANK" in s
    assert "Negotiable" in s
    assert "$100000" in s  # still the required-field fallback


def test_salary_fixed_mode_single(monkeypatch):
    s = _salary(monkeypatch, mode="fixed", fixed="145000")
    assert "fixed answer" in s
    assert "$145000" in s
    assert "Do not adapt it to the posting" in s


def test_salary_fixed_mode_range_and_profile_fallback(monkeypatch):
    s = _salary(monkeypatch, mode="fixed", fixed="140000-160000")
    assert "$140000-160000" in s
    s2 = _salary(monkeypatch, mode="fixed", fixed="")
    assert "$100000" in s2  # falls back to profile expectation

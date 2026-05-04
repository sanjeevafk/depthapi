from scripts.ingest_corpus.ingest_local_repos import (
    _is_eligible_file,
    _is_english_path,
    _matches_any,
)


def test_matches_any_supports_globs():
    assert _matches_any("en-us/docs/index.md", ["en-us/**/*.md"])
    assert not _matches_any("fr/docs/index.md", ["en-us/**/*.md"])


def test_english_filter_accepts_allowlisted_paths():
    assert _is_english_path("en-us/web/api/fetch/index.md", ["en-us/**"])


def test_english_filter_rejects_locale_paths():
    assert not _is_english_path("fr/docs/index.md", [])
    assert not _is_english_path("zh-cn/content/file.md", [])


def test_eligible_file_rejects_binary_and_excluded_patterns():
    source = {
        "include_globs": ["**/*.md"],
        "exclude_globs": ["**/.git/**", "**/images/**"],
        "exclude_noisy": ["**/genindex*"],
    }
    ok, reason = _is_eligible_file("images/logo.png", source)
    assert not ok and reason == "binary_or_static"

    ok, reason = _is_eligible_file(".git/config", source)
    assert not ok and reason == "binary_or_static"

    ok, reason = _is_eligible_file("docs/genindex-all.md", source)
    assert not ok and reason == "noisy_page"

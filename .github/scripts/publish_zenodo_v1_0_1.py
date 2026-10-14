from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path


API = "https://zenodo.org/api"
DRAFT_ID = 23313043
SOURCE_ID = 22252122
CONCEPT_ID = "22252121"
TITLE = "Feed Ingredient Database Toolkit"
VERSION = "v1.0.1"
PUBLICATION_DATE = "2026-10-12"
ARCHIVE_NAME = "feed-ingredient-database-toolkit-v1.0.1.zip"
ARCHIVE_PREFIX = "feed-ingredient-database-toolkit-v1.0.1/"
EXPECTED_TAG_FILE_COUNT = 122
GITHUB_RELEASE = "https://github.com/zengzhengcheng/feed-ingredient-database-toolkit/releases/tag/v1.0.1"
SCIENCEDB_DOI = "10.57760/sciencedb.46393"

METADATA = {
    "metadata": {
        "title": TITLE,
        "upload_type": "software",
        "description": (
            "<p>Feed Ingredient Database Toolkit v1.0.1 is a correction release for review-first "
            "Excel and JSON import, validation, SQLite query and export, alias-dictionary and "
            "metric-catalog management, traceable unit resolution, and a PyQt6 desktop interface.</p>"
            "<p>This version documents editable MJ/kg defaults for omitted energy units, applies the "
            "approved percent rule to AID/ATTD gross-energy digestibility coefficients, and corrects "
            "the synthetic SID Lysine JSON example to 94 percentage points. Existing explicit units "
            "and reported values are preserved. The archived NE/ME model files and results are "
            "unchanged and no model was retrained.</p>"
            "<p>The software archive does not contain the formal ScienceDB SQLite database, the 110 "
            "public data workbooks, source articles, manuscript files, private review material, or "
            "user working databases. The currently cited ScienceDB DOI identifies the published V3 "
            "dataset; the local reviewer-revision data candidate is not represented as already public.</p>"
        ),
        "creators": [
            {"name": "Zeng, Zhengcheng", "orcid": "0009-0007-6385-0458"}
        ],
        "publication_date": PUBLICATION_DATE,
        "access_right": "open",
        "license": "mit",
        "version": VERSION,
        "language": "eng",
        "keywords": [
            "feed ingredients",
            "animal nutrition",
            "SQLite",
            "PyQt6",
            "data curation",
            "Excel import",
            "data provenance",
        ],
        "related_identifiers": [
            {
                "identifier": GITHUB_RELEASE,
                "relation": "isIdenticalTo",
                "resource_type": "software",
            },
            {
                "identifier": SCIENCEDB_DOI,
                "relation": "isSupplementTo",
                "resource_type": "dataset",
            },
        ],
        "notes": (
            "Companion software for the separately archived ScienceDB dataset. The MIT License "
            "applies to this software release only and does not determine the data or source-article "
            "licences. No unpublished article is linked."
        ),
    }
}


class PublicationError(RuntimeError):
    pass


class RequestError(PublicationError):
    def __init__(self, entry: dict[str, object], *, unknown_outcome: bool = False):
        super().__init__(json.dumps(entry, ensure_ascii=False))
        self.entry = entry
        self.unknown_outcome = unknown_outcome


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run(command: list[str], *, cwd: Path) -> bytes:
    return subprocess.check_output(command, cwd=cwd)


def git_paths(source: Path) -> list[str]:
    raw = run(
        ["git", "-c", "core.quotepath=false", "ls-tree", "-r", "-z", "--name-only", "HEAD"],
        cwd=source,
    )
    return [item.decode("utf-8") for item in raw.split(b"\0") if item]


def git_blob(source: Path, relative: str) -> bytes:
    return run(["git", "show", f"HEAD:{relative}"], cwd=source)


def build_and_verify_archive(source: Path, destination: Path) -> dict[str, object]:
    tags = run(["git", "tag", "--points-at", "HEAD"], cwd=source).decode("utf-8").splitlines()
    if VERSION not in tags:
        raise PublicationError(f"Checkout HEAD is not fixed tag {VERSION}: {tags}")
    if run(["git", "status", "--short", "--untracked-files=no"], cwd=source).strip():
        raise PublicationError("Fixed-tag checkout has tracked modifications")
    paths = git_paths(source)
    if len(paths) != EXPECTED_TAG_FILE_COUNT or len(set(paths)) != len(paths):
        raise PublicationError(f"Tag file-count gate failed: {len(paths)}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        destination,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for relative in paths:
            info = zipfile.ZipInfo(
                ARCHIVE_PREFIX + relative,
                date_time=(2026, 10, 12, 16, 4, 12),
            )
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o100644 & 0xFFFF) << 16
            archive.writestr(
                info,
                git_blob(source, relative),
                compress_type=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            )
    with zipfile.ZipFile(destination) as archive:
        archive_paths = [
            name[len(ARCHIVE_PREFIX):]
            for name in archive.namelist()
            if not name.endswith("/") and name.startswith(ARCHIVE_PREFIX)
        ]
        if archive_paths != paths:
            raise PublicationError("Archive path/order gate failed")
        differences = [
            relative
            for relative in paths
            if archive.read(ARCHIVE_PREFIX + relative) != git_blob(source, relative)
        ]
        if differences:
            raise PublicationError(f"Archive direct-content gate failed: {differences[:5]}")
        with tempfile.TemporaryDirectory(prefix="feeddb-zenodo-zip-") as temporary:
            extracted_root = Path(temporary)
            archive.extractall(extracted_root)
            extracted = extracted_root / ARCHIVE_PREFIX.rstrip("/")
            extracted_paths = sorted(
                item.relative_to(extracted).as_posix()
                for item in extracted.rglob("*")
                if item.is_file()
            )
            if extracted_paths != sorted(paths):
                raise PublicationError("Ordinary ZIP extraction path gate failed")
            extracted_differences = [
                relative
                for relative in paths
                if (extracted / relative).read_bytes() != git_blob(source, relative)
            ]
            if extracted_differences:
                raise PublicationError(
                    f"Ordinary ZIP extraction content gate failed: {extracted_differences[:5]}"
                )
    return {
        "tag": VERSION,
        "format": "ZIP_DEFLATED",
        "file_count": len(paths),
        "archive_size_bytes": destination.stat().st_size,
        "path_set_matches_tag": True,
        "direct_content_matches_tag": True,
        "ordinary_extraction_verified": True,
    }


def safe_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def parse_json(body: bytes) -> object | None:
    if not body:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def error_detail(body: bytes) -> dict[str, object]:
    parsed = parse_json(body)
    if isinstance(parsed, dict):
        return {
            "status": parsed.get("status"),
            "message": parsed.get("message"),
            "errors": parsed.get("errors"),
        }
    if body:
        return {
            "body_type": "non_json",
            "body_preview": body.decode("utf-8", errors="replace")[:1000],
        }
    return {"body_type": "empty"}


def scrub_secret(text: str, secret: str | None = None) -> str:
    value = secret if secret is not None else os.environ.get("ZENODO_TOKEN", "")
    return text.replace(value, "[redacted]") if value else text


def record_summary(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    metadata = value.get("metadata") if isinstance(value.get("metadata"), dict) else {}
    reserved = metadata.get("prereserve_doi") if isinstance(metadata.get("prereserve_doi"), dict) else {}
    files = []
    for item in value.get("files") or []:
        if isinstance(item, dict):
            files.append(
                {
                    "filename": item.get("filename") or item.get("key") or item.get("name"),
                    "size": item.get("filesize") or item.get("size"),
                }
            )
    return {
        "id": value.get("id"),
        "state": value.get("state"),
        "status": value.get("status"),
        "submitted": value.get("submitted"),
        "title": metadata.get("title") or value.get("title"),
        "version": metadata.get("version"),
        "publication_date": metadata.get("publication_date"),
        "license": metadata.get("license"),
        "conceptrecid": str(value.get("conceptrecid") or metadata.get("conceptrecid") or ""),
        "doi": metadata.get("doi") or value.get("doi"),
        "conceptdoi": metadata.get("conceptdoi") or value.get("conceptdoi"),
        "reserved_doi": reserved.get("doi"),
        "related_identifiers": metadata.get("related_identifiers") or [],
        "files": files,
    }


class Client:
    def __init__(self, token: str, result: dict[str, object], result_path: Path):
        self.token = token
        self.result = result
        self.result_path = result_path

    def save(self) -> None:
        serialized = json.dumps(self.result, ensure_ascii=False, indent=2) + "\n"
        self.result_path.write_text(scrub_secret(serialized, self.token), encoding="utf-8")

    def request(
        self,
        operation: str,
        method: str,
        url: str,
        *,
        expected: set[int],
        json_value: object | None = None,
        body: bytes | None = None,
        content_type: str | None = None,
        timeout: int = 120,
    ) -> tuple[int, bytes, object | None]:
        data = body
        parsed_url = urllib.parse.urlsplit(url)
        if parsed_url.scheme != "https":
            raise PublicationError("Only HTTPS requests are allowed")
        is_zenodo = parsed_url.hostname == "zenodo.org"
        if not is_zenodo and method != "GET":
            raise PublicationError("Authenticated writes are restricted to zenodo.org")
        headers = {
            "Accept": "application/json",
            "User-Agent": "feed-ingredient-database-toolkit-zenodo-v1.0.1",
        }
        if is_zenodo:
            headers["Authorization"] = f"Bearer {self.token}"
        if json_value is not None:
            data = json.dumps(json_value, ensure_ascii=False).encode("utf-8")
            content_type = "application/json"
        if content_type:
            headers["Content-Type"] = content_type
        if data is not None:
            headers["Content-Length"] = str(len(data))
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        entry: dict[str, object] = {
            "timestamp_utc": utc_now(),
            "operation": operation,
            "method": method,
            "url": safe_url(url),
            "expected_statuses": sorted(expected),
        }
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status = response.status
                response_body = response.read()
        except urllib.error.HTTPError as exc:
            status = exc.code
            response_body = exc.read()
            entry["http_status"] = status
            if status in expected:
                entry["expected_response"] = error_detail(response_body)
                self.result.setdefault("requests", []).append(entry)
                self.save()
                return status, response_body, parse_json(response_body)
            entry["error_response"] = error_detail(response_body)
            self.result.setdefault("requests", []).append(entry)
            self.save()
            raise RequestError(entry, unknown_outcome=False) from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            entry["http_status"] = None
            entry["transport_error"] = f"{type(exc).__name__}: {exc}"
            self.result.setdefault("requests", []).append(entry)
            self.save()
            raise RequestError(entry, unknown_outcome=method in {"POST", "PUT", "DELETE"}) from exc
        entry["http_status"] = status
        if status not in expected:
            entry["error_response"] = error_detail(response_body)
            self.result.setdefault("requests", []).append(entry)
            self.save()
            raise RequestError(entry, unknown_outcome=False)
        parsed = parse_json(response_body)
        if isinstance(parsed, dict) and ("metadata" in parsed or "conceptrecid" in parsed):
            entry["response"] = record_summary(parsed)
        elif isinstance(parsed, dict):
            entry["response"] = {
                key: parsed.get(key)
                for key in ("key", "filename", "size", "status", "message")
                if key in parsed
            }
        else:
            entry["response_bytes"] = len(response_body)
        self.result.setdefault("requests", []).append(entry)
        self.save()
        return status, response_body, parsed

    def get_json_allow_404(self, operation: str, url: str) -> tuple[int, object | None]:
        status, _, parsed = self.request(operation, "GET", url, expected={200, 404}, timeout=60)
        return status, parsed

    def public_probe(self, operation: str, url: str) -> tuple[int, object | None]:
        status, _, parsed = self.request(
            operation,
            "GET",
            url,
            expected={200, 404, 500, 502, 503, 504},
            timeout=60,
        )
        return status, parsed

    def get_bytes(self, operation: str, url: str, *, expected_size: int | None = None) -> bytes:
        status, body, parsed = self.request(operation, "GET", url, expected={200}, timeout=180)
        if status != 200:
            raise PublicationError(f"Download failed: {operation}")
        if isinstance(parsed, dict):
            raise PublicationError(f"Download URL returned JSON metadata, not file content: {safe_url(url)}")
        if expected_size is not None and len(body) != expected_size:
            raise PublicationError(
                f"Downloaded file size mismatch: actual={len(body)}, expected={expected_size}"
            )
        return body


def has_relation(record: dict[str, object], identifier: str, relation: str) -> bool:
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    return any(
        isinstance(item, dict)
        and item.get("identifier") == identifier
        and item.get("relation") == relation
        for item in metadata.get("related_identifiers") or []
    )


def assert_draft(record: object, *, require_file: bool, archive_size: int) -> dict[str, object]:
    if not isinstance(record, dict):
        raise PublicationError("Draft response is not an object")
    summary = record_summary(record)
    if summary["id"] != DRAFT_ID or summary["conceptrecid"] != CONCEPT_ID:
        raise PublicationError(f"Draft identity mismatch: {summary}")
    if summary["state"] != "unsubmitted" or summary["submitted"] is not False:
        raise PublicationError(f"Draft is not editable: {summary}")
    if summary["title"] != TITLE or summary["version"] != VERSION:
        raise PublicationError(f"Draft version/title mismatch: {summary}")
    if summary["publication_date"] != PUBLICATION_DATE:
        raise PublicationError(f"Draft publication date mismatch: {summary}")
    licence = summary["license"]
    if isinstance(licence, dict):
        licence = licence.get("id")
    if str(licence).casefold() not in {"mit", "mit-license"}:
        raise PublicationError(f"Draft licence mismatch: {summary}")
    if not has_relation(record, GITHUB_RELEASE, "isIdenticalTo"):
        raise PublicationError("Draft GitHub v1.0.1 relation is missing")
    if not has_relation(record, SCIENCEDB_DOI, "isSupplementTo"):
        raise PublicationError("Draft ScienceDB relation is missing")
    expected_files = [{"filename": ARCHIVE_NAME, "size": archive_size}]
    if require_file and summary["files"] != expected_files:
        raise PublicationError(f"Draft file gate failed: {summary['files']}")
    return record


def assert_public(record: object, *, archive_size: int) -> dict[str, object]:
    if not isinstance(record, dict):
        raise PublicationError("Public record response is not an object")
    summary = record_summary(record)
    if summary["id"] != DRAFT_ID or summary["conceptrecid"] != CONCEPT_ID:
        raise PublicationError(f"Public record identity mismatch: {summary}")
    if summary["version"] != VERSION or summary["title"] != TITLE:
        raise PublicationError(f"Public record version/title mismatch: {summary}")
    if summary["submitted"] is not True or summary["state"] != "done":
        raise PublicationError(f"Public record is not published: {summary}")
    if not summary["doi"]:
        raise PublicationError("Published record lacks a fixed DOI")
    if summary["files"] != [{"filename": ARCHIVE_NAME, "size": archive_size}]:
        raise PublicationError(f"Public file gate failed: {summary['files']}")
    if not has_relation(record, GITHUB_RELEASE, "isIdenticalTo"):
        raise PublicationError("Public GitHub v1.0.1 relation is missing")
    return record


def draft_download_url(client: Client, draft: dict[str, object], filename: str) -> str:
    files = draft.get("files") or []
    target = next(
        (
            item
            for item in files
            if isinstance(item, dict)
            and (item.get("filename") or item.get("key") or item.get("name")) == filename
        ),
        None,
    )
    if not isinstance(target, dict):
        raise PublicationError("Target draft file is absent")
    links = target.get("links") if isinstance(target.get("links"), dict) else {}
    for key in ("download", "content"):
        if links.get(key):
            return str(links[key])
    self_url = links.get("self")
    if self_url and "/deposit/depositions/" in str(self_url):
        _, _, metadata = client.request(
            "get_draft_file_metadata",
            "GET",
            str(self_url),
            expected={200},
            timeout=60,
        )
        if isinstance(metadata, dict):
            metadata_links = metadata.get("links") if isinstance(metadata.get("links"), dict) else {}
            for key in ("download", "content"):
                if metadata_links.get(key):
                    return str(metadata_links[key])
    draft_links = draft.get("links") if isinstance(draft.get("links"), dict) else {}
    bucket = draft_links.get("bucket")
    if bucket:
        return f"{bucket}/{urllib.parse.quote(filename)}?download=1"
    raise PublicationError("No draft file download URL is available")


def verify_draft_file(client: Client, draft: dict[str, object], archive_bytes: bytes) -> None:
    url = draft_download_url(client, draft, ARCHIVE_NAME)
    downloaded = client.get_bytes(
        "download_draft_archive_for_direct_comparison",
        url,
        expected_size=len(archive_bytes),
    )
    if downloaded != archive_bytes:
        raise PublicationError("Draft archive differs from the fixed-tag archive")


def public_download_url(record: dict[str, object]) -> str:
    files = record.get("files") or []
    if len(files) != 1 or not isinstance(files[0], dict):
        raise PublicationError("Published record does not contain exactly one file")
    links = files[0].get("links") if isinstance(files[0].get("links"), dict) else {}
    for key in ("self", "content", "download"):
        if links.get(key):
            return str(links[key])
    raise PublicationError("Published file lacks a download URL")


def verify_public(
    client: Client,
    record: dict[str, object],
    archive_bytes: bytes,
) -> dict[str, object]:
    assert_public(record, archive_size=len(archive_bytes))
    downloaded = client.get_bytes(
        "download_public_archive_for_direct_comparison",
        public_download_url(record),
        expected_size=len(archive_bytes),
    )
    if downloaded != archive_bytes:
        raise PublicationError("Public archive differs from the fixed-tag archive")
    _, _, versions = client.request(
        "read_public_version_family",
        "GET",
        f"{API}/records/{DRAFT_ID}/versions",
        expected={200},
        timeout=60,
    )
    hits: list[dict[str, object]] = []
    if isinstance(versions, dict):
        container = versions.get("hits")
        if isinstance(container, dict):
            hits = [item for item in container.get("hits") or [] if isinstance(item, dict)]
    version_summaries = [record_summary(item) for item in hits]
    versions_seen = {item.get("version") for item in version_summaries}
    if not {"v1.0.0", VERSION}.issubset(versions_seen):
        raise PublicationError(f"Version-family gate failed: {version_summaries}")
    summary = record_summary(record)
    return {
        "record_id": summary["id"],
        "version_doi": summary["doi"],
        "concept_doi": summary["conceptdoi"],
        "version": summary["version"],
        "file": summary["files"][0],
        "direct_public_content_match": True,
        "versions": version_summaries,
    }


def publish_existing_draft(
    source: Path,
    result_path: Path,
    *,
    archive_only: bool,
) -> dict[str, object]:
    result: dict[str, object] = {
        "started_utc": utc_now(),
        "status": "running",
        "draft_id": DRAFT_ID,
        "source_record_id": SOURCE_ID,
        "concept_id": CONCEPT_ID,
        "target_version": VERSION,
        "requests": [],
    }
    archive_path = result_path.parent / ARCHIVE_NAME
    result["archive"] = build_and_verify_archive(source, archive_path)
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if archive_only:
        result["status"] = "archive_verified_only"
        result["completed_utc"] = utc_now()
        result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return result
    token = os.environ.get("ZENODO_TOKEN", "")
    if not token:
        raise PublicationError("ZENODO_TOKEN secret is unavailable")
    client = Client(token, result, result_path)
    archive_bytes = archive_path.read_bytes()

    public_status, public_record = client.public_probe(
        "check_existing_public_record",
        f"{API}/records/{DRAFT_ID}",
    )
    if public_status == 200:
        assert isinstance(public_record, dict)
        result["publication"] = verify_public(client, public_record, archive_bytes)
        result["status"] = "already_published_verified"
        result["completed_utc"] = utc_now()
        client.save()
        return result

    draft_status, _, draft = client.request(
        "read_existing_draft",
        "GET",
        f"{API}/deposit/depositions/{DRAFT_ID}",
        expected={200, 404},
        timeout=60,
    )
    if draft_status == 404:
        for attempt in range(1, 4):
            retry_status, retry_record = client.public_probe(
                f"public_recovery_after_missing_draft_attempt_{attempt}",
                f"{API}/records/{DRAFT_ID}",
            )
            if retry_status == 200 and isinstance(retry_record, dict):
                result["publication"] = verify_public(client, retry_record, archive_bytes)
                result["status"] = "already_published_verified"
                result["completed_utc"] = utc_now()
                client.save()
                return result
            if attempt < 3:
                time.sleep(5)
        raise PublicationError("Draft is absent and the public record could not be verified")
    if not isinstance(draft, dict):
        raise PublicationError("Existing draft was not returned")
    assert_draft(draft, require_file=False, archive_size=len(archive_bytes))

    _, _, updated = client.request(
        "enforce_v1.0.1_metadata",
        "PUT",
        f"{API}/deposit/depositions/{DRAFT_ID}",
        expected={200},
        json_value=METADATA,
        timeout=120,
    )
    if not isinstance(updated, dict):
        raise PublicationError("Updated draft was not returned")
    draft = assert_draft(updated, require_file=False, archive_size=len(archive_bytes))

    files = draft.get("files") or []
    if len(files) > 1:
        raise PublicationError("Draft contains multiple files; refusing destructive cleanup")
    if len(files) == 1:
        summary = record_summary(draft)
        expected = [{"filename": ARCHIVE_NAME, "size": len(archive_bytes)}]
        if summary["files"] != expected:
            raise PublicationError(f"Draft contains an unexpected file: {summary['files']}")
        verify_draft_file(client, draft, archive_bytes)
    else:
        links = draft.get("links") if isinstance(draft.get("links"), dict) else {}
        bucket = links.get("bucket")
        if not bucket:
            raise PublicationError("Draft lacks a bucket upload URL")
        upload_url = f"{bucket}/{urllib.parse.quote(ARCHIVE_NAME)}"
        try:
            client.request(
                "upload_fixed_tag_archive",
                "PUT",
                upload_url,
                expected={200, 201},
                body=archive_bytes,
                content_type="application/octet-stream",
                timeout=300,
            )
        except RequestError as exc:
            if not exc.unknown_outcome:
                raise
        _, _, readback = client.request(
            "readback_after_upload",
            "GET",
            f"{API}/deposit/depositions/{DRAFT_ID}",
            expected={200},
            timeout=60,
        )
        draft = assert_draft(readback, require_file=True, archive_size=len(archive_bytes))
        verify_draft_file(client, draft, archive_bytes)

    _, _, final_draft = client.request(
        "final_draft_gate",
        "GET",
        f"{API}/deposit/depositions/{DRAFT_ID}",
        expected={200},
        timeout=60,
    )
    draft = assert_draft(final_draft, require_file=True, archive_size=len(archive_bytes))
    verify_draft_file(client, draft, archive_bytes)

    publish_error: RequestError | None = None
    try:
        client.request(
            "publish_existing_draft_once",
            "POST",
            f"{API}/deposit/depositions/{DRAFT_ID}/actions/publish",
            expected={200, 201, 202},
            timeout=300,
        )
    except RequestError as exc:
        publish_error = exc
        if not exc.unknown_outcome:
            raise

    published_record: dict[str, object] | None = None
    for attempt in range(1, 7):
        status, candidate = client.public_probe(
            f"public_readback_after_publish_attempt_{attempt}",
            f"{API}/records/{DRAFT_ID}",
        )
        if status == 200 and isinstance(candidate, dict):
            published_record = candidate
            break
        if attempt < 6:
            time.sleep(5)
    if published_record is None:
        if publish_error is not None:
            raise publish_error
        raise PublicationError("Publish returned but the public record did not become readable")

    result["publication"] = verify_public(client, published_record, archive_bytes)
    result["status"] = "published_verified"
    result["completed_utc"] = utc_now()
    client.save()
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--archive-only", action="store_true")
    args = parser.parse_args()
    source = args.source.resolve()
    result_path = args.result.resolve()
    try:
        result = publish_existing_draft(source, result_path, archive_only=args.archive_only)
    except Exception as exc:
        if result_path.exists():
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                result = {}
        else:
            result = {}
        result["status"] = "failed"
        result["failed_utc"] = utc_now()
        result["error"] = {
            "type": type(exc).__name__,
            "message": scrub_secret(str(exc)[:4000]),
        }
        serialized = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        result_path.write_text(scrub_secret(serialized), encoding="utf-8")
        print(
            "ZENODO_PUBLICATION_FAILED="
            + scrub_secret(json.dumps(result["error"], ensure_ascii=False))
        )
        return 1
    publication = result.get("publication") if isinstance(result.get("publication"), dict) else {}
    print(
        "ZENODO_RESULT_JSON="
        + json.dumps(
            {
                "status": result.get("status"),
                "record_id": publication.get("record_id"),
                "version_doi": publication.get("version_doi"),
                "concept_doi": publication.get("concept_doi"),
                "archive": result.get("archive"),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

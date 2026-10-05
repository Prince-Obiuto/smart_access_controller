import hashlib
import io
import warnings
from pathlib import Path
from uuid import uuid4
from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader
from pypdf.errors import PdfReadError
from .models import Upload
from .security import DomainError, utcnow

Image.MAX_IMAGE_PIXELS = 20_000_000


def validate_content(content: bytes, photo=False):
    try:
        if content.startswith(b"%PDF-") and not photo:
            reader = PdfReader(io.BytesIO(content), strict=True)
            if reader.is_encrypted or not 1 <= len(reader.pages) <= 500:
                raise ValueError("Encrypted or excessive PDF.")
            forbidden = {
                "/JS",
                "/JavaScript",
                "/Launch",
                "/EmbeddedFiles",
                "/OpenAction",
                "/AA",
                "/RichMedia",
                "/XFA",
            }
            seen = set()

            def inspect(obj, depth=0):
                if depth > 80:
                    raise ValueError("PDF nesting exceeds limit.")
                if hasattr(obj, "get_object"):
                    obj = obj.get_object()
                if id(obj) in seen:
                    return
                seen.add(id(obj))
                if isinstance(obj, dict):
                    if forbidden.intersection(obj):
                        raise ValueError("Active PDF content is not accepted.")
                    for v in obj.values():
                        inspect(v, depth + 1)
                elif isinstance(obj, list):
                    for v in obj:
                        inspect(v, depth + 1)

            inspect(reader.trailer)
            for page in reader.pages:
                page.get_contents()
            return content, "application/pdf", ".pdf"
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as image:
                kind = image.format
                if kind not in ("JPEG", "PNG"):
                    raise ValueError("Unsupported image format.")
                image.verify()
            with Image.open(io.BytesIO(content)) as image:
                image.load()
                if image.width * image.height > 20_000_000:
                    raise ValueError("Image too large.")
                # Strip EXIF/metadata and encode canonical photo bytes.
                if photo:
                    output = io.BytesIO()
                    image.convert("RGB").save(output, format="JPEG", quality=90)
                    return output.getvalue(), "image/jpeg", ".jpg"
                return (
                    content,
                    "image/jpeg" if kind == "JPEG" else "image/png",
                    ".jpg" if kind == "JPEG" else ".png",
                )
    except (
        ValueError,
        KeyError,
        TypeError,
        RecursionError,
        OSError,
        UnidentifiedImageError,
        PdfReadError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise DomainError(
            "VALIDATION_ERROR",
            "Malformed or unsafe file. Use a valid JPEG, PNG or passive PDF.",
        ) from exc


def save_upload(db, settings, person_id, purpose, filename, content, limit):
    if not content or len(content) > limit:
        raise DomainError(
            "VALIDATION_ERROR",
            f"File must be nonempty and no larger than {limit // 1048576} MiB.",
        )
    content, mime, suffix = validate_content(content, purpose == "PHOTO")
    if len(content) > limit:
        raise DomainError(
            "VALIDATION_ERROR", "Decoded photo exceeds the configured storage limit."
        )
    settings.upload_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    stored = uuid4().hex + suffix
    path = settings.upload_dir / stored
    with path.open("xb") as out:
        out.write(content)
    path.chmod(0o600)
    name = Path((filename or "upload").replace("\\", "/")).name
    name = "".join(c for c in name if c.isprintable())[:180] or "upload" + suffix
    row = Upload(
        person_id=person_id,
        purpose=purpose,
        stored_name=stored,
        name=name,
        content_type=mime,
        size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        created_at=utcnow(),
    )
    db.info.setdefault("new_upload_paths", []).append(path)
    db.add(row)
    db.flush()
    return row


def remove_upload(db, settings, row):
    # Storage keys originate here, never from user-controlled path segments.
    path = (settings.upload_dir / row.stored_name).resolve()
    if path.parent != settings.upload_dir.resolve():
        raise RuntimeError("Invalid stored upload key.")
    path.unlink(missing_ok=True)
    db.delete(row)

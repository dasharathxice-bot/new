import io
import re
import os
import logging
from enum import Enum
from typing import Tuple, Dict, Any, Optional, List
from PIL import Image

logger = logging.getLogger("framex.proof")

# Try importing RapidOCR for highly accurate local OCR
try:
    from rapidocr_onnxruntime import RapidOCR
    _rapid_ocr_engine = RapidOCR()
except Exception as e:
    logger.warning(f"RapidOCR engine initialization failed: {e}")
    _rapid_ocr_engine = None

# Fallback: pytesseract if tesseract binary is available on the system
import pytesseract
possible_paths = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
]
for p in possible_paths:
    if os.path.exists(p):
        pytesseract.pytesseract.tesseract_cmd = p
        break

# Optional Vision Model via Groq
try:
    from groq import AsyncGroq
except ImportError:
    AsyncGroq = None


class DecisionOutcome(Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"


class ProofVerifier:
    def __init__(self):
        self.groq_api_key = os.getenv("GROQ_API_KEY", "")
        self.groq_client = AsyncGroq(api_key=self.groq_api_key) if (self.groq_api_key and AsyncGroq) else None

    async def fetch_image(self, url: str, max_size_bytes: int = 15 * 1024 * 1024) -> bytes:
        """
        Asynchronously downloads and validates image with size limits and timeouts.
        Fails closed on any error or timeout.
        """
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status != 200:
                    raise ValueError(f"HTTP error {resp.status} downloading image")

                content_length = resp.headers.get("Content-Length")
                if content_length and int(content_length) > max_size_bytes:
                    raise ValueError(f"Image exceeds maximum allowed size of {max_size_bytes // (1024*1024)}MB")

                data = await resp.read()
                if len(data) > max_size_bytes:
                    raise ValueError(f"Image exceeds maximum allowed size of {max_size_bytes // (1024*1024)}MB")
                if len(data) == 0:
                    raise ValueError("Received empty image attachment")
                return data

    def validate_attachment(self, image_bytes: bytes) -> Tuple[bool, Optional[Image.Image], str]:
        """
        Stage A — Attachment Validation.
        - Validates actual image content rather than trusting filename or declared MIME type.
        - Enforces supported formats: JPEG, PNG, WebP.
        - Rejects empty, corrupt, excessively large, or decompression-bomb files.
        - Validates dimensions and aspect ratios.
        """
        if not image_bytes or len(image_bytes) < 32:
            return False, None, "The uploaded file is empty or too small to be a valid image."

        try:
            image = Image.open(io.BytesIO(image_bytes))
            
            # Check format
            fmt = (image.format or "").upper()
            if fmt not in ("PNG", "JPEG", "JPG", "WEBP"):
                return False, None, f"Unsupported file format ({fmt or 'unknown'}). Please upload a genuine PNG, JPEG, or WebP screenshot."

            # Verify image is not corrupted by loading pixel data
            image.load()

            width, height = image.size

            # Enforce reasonable screenshot dimensions
            if width < 300 or height < 300:
                return False, None, f"Image resolution ({width}x{height}) is too low to reliably verify text. Please upload a full-resolution screenshot."

            # Safe decompression bomb guard
            if width * height > 35_000_000:
                return False, None, "Image dimensions are excessively large. Please upload a standard screenshot."

            # Aspect ratio check (typical mobile or desktop screenshot)
            aspect_ratio = max(width, height) / max(min(width, height), 1)
            if aspect_ratio > 4.5:
                return False, None, "The uploaded image has an irregular aspect ratio. Please upload a full, uncropped screenshot."

            return True, image, ""

        except Exception as e:
            logger.error(f"Image decode error: {e}")
            return False, None, f"Could not decode image file: {e}"

    def extract_text_and_confidence(self, image: Image.Image) -> Tuple[str, float]:
        """
        Extracts visible text and average OCR confidence using RapidOCR (with Tesseract fallback).
        Returns (extracted_text_lowercase, average_confidence).
        Fail-closed: Returns ('', 0.0) if OCR fails or finds no text.
        """
        # 1. Try RapidOCR
        if _rapid_ocr_engine is not None:
            try:
                buf = io.BytesIO()
                # Save as PNG for OCR engine consumption
                image.convert("RGB").save(buf, format="PNG")
                ocr_results, _ = _rapid_ocr_engine(buf.getvalue())
                
                if ocr_results:
                    lines = []
                    scores = []
                    for item in ocr_results:
                        # item format: [box, text, score]
                        if len(item) >= 3:
                            lines.append(str(item[1]))
                            try:
                                scores.append(float(item[2]))
                            except (ValueError, TypeError):
                                scores.append(0.0)
                    full_text = " ".join(lines).lower()
                    avg_conf = sum(scores) / max(len(scores), 1)
                    return full_text, avg_conf
            except Exception as e:
                logger.warning(f"RapidOCR extraction error: {e}")

        # 2. Try pytesseract fallback
        try:
            gray = image.convert('L')
            data = pytesseract.image_to_data(gray, output_type=pytesseract.Output.DICT)
            text_parts = []
            conf_scores = []
            for i, text in enumerate(data.get('text', [])):
                cleaned = text.strip()
                if cleaned:
                    text_parts.append(cleaned)
                    try:
                        c = float(data.get('conf', [0])[i])
                        if c >= 0:
                            conf_scores.append(c / 100.0)
                    except (ValueError, TypeError, IndexError):
                        pass
            full_text = " ".join(text_parts).lower()
            avg_conf = sum(conf_scores) / max(len(conf_scores), 1) if conf_scores else 0.0
            return full_text, avg_conf
        except Exception as e:
            logger.debug(f"Pytesseract fallback OCR unavailable or failed: {e}")

        return "", 0.0

    async def verify_youtube_subscription(self, text: str, image: Image.Image) -> Tuple[DecisionOutcome, str]:
        """
        Dedicated verification pipeline for YouTube Subscription.
        Required:
        - Evidence of YouTube platform
        - Exact channel handle: @framexfpsz
        - Active Subscribed status (e.g. 'subscribed', 'abonné', etc.)
        Rejects:
        - Unsubscribed status ('subscribe' button active)
        - Search results or mentions only
        - Missing or incorrect channel
        """
        # Normalize text: collapse whitespace and remove common OCR artifact spaces in handle
        norm_text = re.sub(r'\s+', ' ', text)
        handle_clean_text = re.sub(r'[@\s]', '', text)

        # Stage B: Platform detection
        yt_platform_markers = [
            "youtube", "subscribers", "subscribed", "subscribe", "notification",
            "notifications", "bell", "shorts", "playlists", "community", "videos",
            "framexfpsz", "channel"
        ]
        has_platform = any(m in norm_text for m in yt_platform_markers)
        if not has_platform:
            return DecisionOutcome.FAIL, "Image does not depict the YouTube interface. Please upload a genuine screenshot of our YouTube channel."

        # Stage C: Exact account identity verification (@framexfpsz)
        # Check for exact handle in normalized variations
        has_exact_handle = (
            "@framexfpsz" in norm_text or
            "framexfpsz" in handle_clean_text or
            bool(re.search(r'framex\s*fpsz', norm_text))
        )
        if not has_exact_handle:
            # Check if user uploaded a different channel or searched for framex without opening channel
            if "framex" in norm_text:
                return DecisionOutcome.FAIL, "Screenshot mentions 'framex' but does not clearly show the exact channel handle '@framexfpsz'. Please open the channel page directly."
            return DecisionOutcome.FAIL, "Could not identify the target YouTube channel '@framexfpsz' in the screenshot."

        # Stage D: Required relationship state (Subscribed)
        # Explicit subscribed indicators across multiple common languages
        subscribed_patterns = [
            r'\bsubscribed\b',
            r'\babonn[ée]\b',
            r'\bsuscrito\b',
            r'\bsuscripto\b',
            r'\baboniert\b',
            r'\binscrit\b',
            r'\bjoined\b',
            r'all notifications',
            r'personalized',
            r'notifications.*(all|on|personalized)'
        ]
        is_subscribed = any(re.search(pat, norm_text) for pat in subscribed_patterns)

        # Unsubscribed / Call-to-action indicators
        unsubscribed_patterns = [
            r'\bsubscribe\b(?!\s*d|\s*rs)',  # matches "subscribe" but not "subscribers" or "subscribed"
            r"\bs'abonner\b",
            r'\bsuscribirse\b',
            r'\babonnieren\b',
            r'\bs’abonner\b'
        ]
        shows_unsubscribed = any(re.search(pat, norm_text) for pat in unsubscribed_patterns)

        if shows_unsubscribed and not is_subscribed:
            return DecisionOutcome.FAIL, "The screenshot shows an active 'Subscribe' button (unsubscribed state). You must click 'Subscribe' before taking the screenshot."

        if is_subscribed:
            return DecisionOutcome.PASS, "YouTube subscription verified: exact channel handle '@framexfpsz' and 'Subscribed' status confirmed."

        # If handle is found but status cannot be read
        return DecisionOutcome.INCONCLUSIVE, "Detected channel '@framexfpsz', but the 'Subscribed' button/status is not clearly visible or unreadable. Please provide an uncropped screenshot showing the 'Subscribed' button."

    async def verify_instagram_follow(self, text: str, image: Image.Image) -> Tuple[DecisionOutcome, str]:
        """
        Dedicated verification pipeline for Instagram Following.
        Required:
        - Evidence of Instagram platform
        - Exact account handle: @framex.hz
        - Active Following status (e.g. 'following', 'siguiendo', etc.)
        Rejects:
        - Unfollowed status ('follow' or 'follow back' button active)
        - Search results or mentions only
        - Missing or incorrect account
        """
        norm_text = re.sub(r'\s+', ' ', text)
        handle_clean_text = re.sub(r'[@\s]', '', text)

        # Stage B: Platform detection
        ig_platform_markers = [
            "instagram", "following", "followers", "posts", "message",
            "threads", "reels", "tagged", "edit profile", "share profile",
            "framex.hz"
        ]
        has_platform = any(m in norm_text for m in ig_platform_markers)
        if not has_platform:
            return DecisionOutcome.FAIL, "Image does not depict the Instagram interface. Please upload a genuine screenshot of our Instagram profile."

        # Stage C: Exact account identity verification (@framex.hz)
        has_exact_account = (
            "@framex.hz" in norm_text or
            "framex.hz" in handle_clean_text or
            bool(re.search(r'framex\s*[\._]\s*hz', norm_text))
        )
        if not has_exact_account:
            if "framex" in norm_text:
                return DecisionOutcome.FAIL, "Screenshot mentions 'framex' but does not clearly show the exact profile username '@framex.hz'. Please open the profile directly."
            return DecisionOutcome.FAIL, "Could not identify the target Instagram profile '@framex.hz' in the screenshot."

        # Stage D: Required relationship state (Following)
        following_patterns = [
            r'\bfollowing\b',
            r'\bsiguiendo\b',
            r'\babonn[ée]\(e\)\b',
            r'\bsuivi\(e\)\b',
            r'\bgefolgt\b',
            r'\brequested\b'
        ]
        is_following = any(re.search(pat, norm_text) for pat in following_patterns)

        # Unfollowed indicators
        unfollowed_patterns = [
            r'\bfollow\b(?!\s*ing|\s*ers)',
            r'\bfollow back\b',
            r'\bseguir\b',
            r'\bse suivre\b'
        ]
        shows_unfollowed = any(re.search(pat, norm_text) for pat in unfollowed_patterns)

        if shows_unfollowed and not is_following:
            return DecisionOutcome.FAIL, "The screenshot shows an active 'Follow' button (non-following state). You must click 'Follow' before taking the screenshot."

        if is_following:
            return DecisionOutcome.PASS, "Instagram follow verified: exact account '@framex.hz' and 'Following' status confirmed."

        return DecisionOutcome.INCONCLUSIVE, "Detected profile '@framex.hz', but the 'Following' status is not clearly visible or unreadable. Please provide an uncropped screenshot showing the 'Following' button."

    async def verify_custom_requirement(self, req: Dict[str, Any], text: str, image: Image.Image) -> Tuple[DecisionOutcome, str]:
        """
        Verification pipeline for custom configured proof requirements.
        Evaluates based on individual requirement URL, name, and instructions.
        Fail-closed: requires definitive proof.
        """
        norm_text = re.sub(r'\s+', ' ', text)
        name = req.get("name", "").lower()
        url = req.get("url", "").lower()

        # Extract domain/service name
        domain_match = re.search(r'https?://(?:www\.)?([^/]+)', url)
        domain = domain_match.group(1).lower() if domain_match else ""

        # Extract target handle from URL path
        handle_match = re.search(r'/(?:@)?([a-zA-Z0-9_\-\.]+)', url.replace(domain, ""))
        target_handle = handle_match.group(1).lower() if handle_match else ""

        # Check domain presence
        if domain and (domain not in norm_text and not any(kw in norm_text for kw in name.split())):
            return DecisionOutcome.FAIL, f"Screenshot does not match requirement '{req.get('name')}'. Platform indicators for {domain} not found."

        # Check target handle presence
        if target_handle and target_handle not in norm_text and target_handle not in re.sub(r'[@\s]', '', text):
            return DecisionOutcome.FAIL, f"Screenshot does not show target account '{target_handle}' for requirement '{req.get('name')}'."

        # Check positive confirmation keywords
        positive_keywords = ["subscribed", "following", "joined", "member", "verified", "completed", "done", "liked"]
        has_positive = any(kw in norm_text for kw in positive_keywords)

        if has_positive:
            return DecisionOutcome.PASS, f"Custom requirement '{req.get('name')}' verified."

        return DecisionOutcome.INCONCLUSIVE, f"Screenshot appears relevant to '{req.get('name')}', but completion status is not clearly established. Please upload a clearer screenshot."

    async def classify_and_verify(
        self,
        image_bytes: bytes,
        active_requirements: List[Dict[str, Any]]
    ) -> Tuple[Optional[str], str, str, Dict[str, Any]]:
        """
        Strict 5-stage verification pipeline for proof screenshots.
        Stage A: Attachment validation (format, dimensions, corruption)
        Stage B: Relevant platform detection
        Stage C: Exact account identity verification
        Stage D: Required relationship state verification
        Stage E: Conservative decision policy (PASS, FAIL, INCONCLUSIVE)

        Returns:
            (matched_req_id, status, explanation, metadata)
            status is one of:
            - 'VERIFIED' (from internal PASS)
            - 'REJECTED' (from internal FAIL)
            - 'NEEDS_REUPLOAD' (from internal INCONCLUSIVE)
        """
        # Stage A: Attachment validation
        is_valid, image, val_err = self.validate_attachment(image_bytes)
        if not is_valid or image is None:
            metadata = {"error": val_err, "stage": "Stage_A_Attachment_Validation"}
            return (None, "NEEDS_REUPLOAD", val_err, metadata)

        width, height = image.size
        metadata = {
            "dimensions": f"{width}x{height}",
            "format": image.format or "PNG",
            "active_req_count": len(active_requirements)
        }

        # Extract text via OCR
        text, avg_conf = self.extract_text_and_confidence(image)
        metadata["text_length"] = len(text)
        metadata["ocr_confidence"] = round(avg_conf, 3)

        # Stage E / Fail-closed: If OCR extracted no text or only minimal noise
        if not text or len(text.strip()) < 5:
            return (
                None,
                "NEEDS_REUPLOAD",
                "We could not detect any readable text in the uploaded screenshot. Please upload a clear, high-resolution screenshot directly from the app or browser.",
                metadata
            )

        # Check for candidate platform indicators
        norm_text = re.sub(r'\s+', ' ', text)
        yt_candidate = any(kw in norm_text for kw in ["youtube", "subscribers", "subscribed", "subscribe", "framexfpsz"])
        ig_candidate = any(kw in norm_text for kw in ["instagram", "following", "followers", "posts", "framex.hz"])

        req_map = {r["id"]: r for r in active_requirements}

        # Try YouTube requirement if active and candidate
        if "youtube_sub" in req_map and (yt_candidate or not ig_candidate):
            outcome, explanation = await self.verify_youtube_subscription(text, image)
            if outcome == DecisionOutcome.PASS:
                return ("youtube_sub", "VERIFIED", explanation, metadata)
            elif outcome == DecisionOutcome.FAIL and yt_candidate:
                return ("youtube_sub", "REJECTED", explanation, metadata)
            elif outcome == DecisionOutcome.INCONCLUSIVE and yt_candidate:
                return ("youtube_sub", "NEEDS_REUPLOAD", explanation, metadata)

        # Try Instagram requirement if active and candidate
        if "instagram_follow" in req_map and (ig_candidate or not yt_candidate):
            outcome, explanation = await self.verify_instagram_follow(text, image)
            if outcome == DecisionOutcome.PASS:
                return ("instagram_follow", "VERIFIED", explanation, metadata)
            elif outcome == DecisionOutcome.FAIL and ig_candidate:
                return ("instagram_follow", "REJECTED", explanation, metadata)
            elif outcome == DecisionOutcome.INCONCLUSIVE and ig_candidate:
                return ("instagram_follow", "NEEDS_REUPLOAD", explanation, metadata)

        # Evaluate any other active requirements (custom links)
        for req_id, req in req_map.items():
            if req_id in ("youtube_sub", "instagram_follow"):
                continue
            outcome, explanation = await self.verify_custom_requirement(req, text, image)
            if outcome == DecisionOutcome.PASS:
                return (req_id, "VERIFIED", explanation, metadata)
            elif outcome == DecisionOutcome.FAIL:
                return (req_id, "REJECTED", explanation, metadata)
            elif outcome == DecisionOutcome.INCONCLUSIVE:
                return (req_id, "NEEDS_REUPLOAD", explanation, metadata)

        # If image does not match any required platform or requirement
        return (
            None,
            "REJECTED",
            "The uploaded image does not appear to show the required YouTube (@framexfpsz) or Instagram (@framex.hz) page. Please upload a clear, uncropped screenshot from the required platform.",
            metadata
        )

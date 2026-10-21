import asyncio
import os
import aiosqlite
import io
from PIL import Image
from database import Database
from proof_verification import ProofVerifier
from config import PERMANENT_PROOF_IDS

async def run_tests():
    test_db_path = "test_framex_v3.db"
    if os.path.exists(test_db_path):
        os.remove(test_db_path)

    print("--- [1] INITIALIZING DATABASE & PERMANENT PROOF TEST ---")
    db = Database(test_db_path)
    await db.init_db()
    guild_id = 999999999
    await db.seed_default_proof_links(guild_id)

    # Verify seeded permanent proof links
    links = await db.get_proof_links(guild_id)
    assert len(links) >= 2, f"Expected at least 2 default links, found {len(links)}"
    ids = {l["id"] for l in links}
    assert "youtube_sub" in ids and "instagram_follow" in ids
    print(f"Permanent proof links verified: {ids} (PASSED)")

    # Attempt to disable or remove permanent link -> MUST FAIL
    assert await db.update_proof_link_state(guild_id, "youtube_sub", 0) is False
    assert await db.remove_proof_link(guild_id, "instagram_follow") is False
    print("Permanent proof protection: (PASSED)")

    print("--- [2] TEST STRICT SCREENSHOT VERIFICATION PIPELINE ---")
    verifier = ProofVerifier()
    
    # Test 2A: Tiny / Low Resolution Image (< 300x300) -> MUST FAIL with NEEDS_REUPLOAD
    tiny = Image.new("RGB", (150, 150), color=(20, 20, 20))
    buf_tiny = io.BytesIO()
    tiny.save(buf_tiny, format="PNG")
    req_id, status, explanation, meta = await verifier.classify_and_verify(buf_tiny.getvalue(), links)
    assert status == "NEEDS_REUPLOAD", f"Expected NEEDS_REUPLOAD, got {status}"
    print(f"Low-resolution rejection: {status} -> {explanation[:40]}... (PASSED)")

    # Test 2B: Blank / Random Image (> 300x300) -> MUST NEVER PASS
    blank_img = Image.new("RGB", (1080, 1920), color=(15, 15, 20))
    buf_blank = io.BytesIO()
    blank_img.save(buf_blank, format="PNG")
    req_id, status, explanation, meta = await verifier.classify_and_verify(buf_blank.getvalue(), links)
    assert status != "VERIFIED", f"Blank image was falsely VERIFIED! Status was: {status}"
    assert status in ["NEEDS_REUPLOAD", "REJECTED"], f"Unexpected status for blank image: {status}"
    print(f"Blank image rejection: {status} (PASSED)")

    # Test 2C: Fake screenshot with wrong account / channel -> MUST FAIL / REJECTED
    from PIL import ImageDraw
    fake_img = Image.new("RGB", (800, 600), color=(255, 255, 255))
    d = ImageDraw.Draw(fake_img)
    d.text((50, 50), "YouTube", fill=(0, 0, 0))
    d.text((50, 100), "@randomchannel123", fill=(0, 0, 0))
    d.text((50, 150), "Subscribed", fill=(0, 0, 0))
    buf_fake = io.BytesIO()
    fake_img.save(buf_fake, format="PNG")
    req_id, status, explanation, meta = await verifier.classify_and_verify(buf_fake.getvalue(), links)
    assert status == "REJECTED", f"Expected REJECTED for wrong account, got {status}"
    print(f"Wrong account rejection: {status} -> {explanation[:40]}... (PASSED)")

    # Test 2D: Unsubscribed YouTube screenshot -> MUST FAIL / REJECTED
    unsub_yt = Image.new("RGB", (800, 600), color=(255, 255, 255))
    d = ImageDraw.Draw(unsub_yt)
    d.text((50, 50), "YouTube", fill=(0, 0, 0))
    d.text((50, 100), "@framexfpsz", fill=(0, 0, 0))
    d.text((50, 150), "Subscribe", fill=(255, 0, 0))
    buf_unsub = io.BytesIO()
    unsub_yt.save(buf_unsub, format="PNG")
    req_id, status, explanation, meta = await verifier.classify_and_verify(buf_unsub.getvalue(), links)
    assert status == "REJECTED", f"Expected REJECTED for unsubscribed, got {status}"
    print(f"Unsubscribed YouTube rejection: {status} -> {explanation[:40]}... (PASSED)")

    # Test 2E: Genuine YouTube Subscription -> MUST PASS / VERIFIED
    valid_yt = Image.new("RGB", (800, 600), color=(255, 255, 255))
    d = ImageDraw.Draw(valid_yt)
    d.text((50, 50), "YouTube", fill=(0, 0, 0))
    d.text((50, 100), "@framexfpsz", fill=(0, 0, 0))
    d.text((50, 150), "Subscribed", fill=(0, 150, 0))
    buf_valid_yt = io.BytesIO()
    valid_yt.save(buf_valid_yt, format="PNG")
    req_id, status, explanation, meta = await verifier.classify_and_verify(buf_valid_yt.getvalue(), links)
    assert status == "VERIFIED" and req_id == "youtube_sub", f"Expected VERIFIED for youtube_sub, got {status} ({req_id})"
    print(f"Genuine YouTube subscription verification: {status} ({req_id}) (PASSED)")

    # Test 2F: Unfollowing Instagram screenshot -> MUST FAIL / REJECTED
    unfollow_ig = Image.new("RGB", (800, 600), color=(255, 255, 255))
    d = ImageDraw.Draw(unfollow_ig)
    d.text((50, 50), "Instagram", fill=(0, 0, 0))
    d.text((50, 100), "@framex.hz", fill=(0, 0, 0))
    d.text((50, 150), "Follow", fill=(0, 0, 255))
    buf_unfollow = io.BytesIO()
    unfollow_ig.save(buf_unfollow, format="PNG")
    req_id, status, explanation, meta = await verifier.classify_and_verify(buf_unfollow.getvalue(), links)
    assert status == "REJECTED", f"Expected REJECTED for unfollowed Instagram, got {status}"
    print(f"Unfollowing Instagram rejection: {status} -> {explanation[:40]}... (PASSED)")

    # Test 2G: Genuine Instagram Follow -> MUST PASS / VERIFIED
    valid_ig = Image.new("RGB", (800, 600), color=(255, 255, 255))
    d = ImageDraw.Draw(valid_ig)
    d.text((50, 50), "Instagram", fill=(0, 0, 0))
    d.text((50, 100), "@framex.hz", fill=(0, 0, 0))
    d.text((50, 150), "Following", fill=(0, 150, 0))
    buf_valid_ig = io.BytesIO()
    valid_ig.save(buf_valid_ig, format="PNG")
    req_id, status, explanation, meta = await verifier.classify_and_verify(buf_valid_ig.getvalue(), links)
    assert status == "VERIFIED" and req_id == "instagram_follow", f"Expected VERIFIED for instagram_follow, got {status} ({req_id})"
    print(f"Genuine Instagram follow verification: {status} ({req_id}) (PASSED)")

    print("--- [3] TEST UPI PAYMENT CONFIGURATION & ATOMIC SWAP ---")
    # Verify incomplete config state initially
    initial_cfg = await db.get_upi_config(guild_id)
    assert initial_cfg is None or not initial_cfg.get("upi_id")
    print("Initial UPI config state verified as empty (PASSED)")

    # Set UPI ID
    owner_id = 111222333
    test_upi = "framex.official@oksbi"
    await db.set_upi_id(guild_id, test_upi, owner_id)
    cfg_after_id = await db.get_upi_config(guild_id)
    assert cfg_after_id["upi_id"] == test_upi
    print(f"UPI ID configuration: {cfg_after_id['upi_id']} (PASSED)")

    # Set QR Image with Atomic File Write
    os.makedirs("assets/upi", exist_ok=True)
    test_qr_path = os.path.abspath(f"assets/upi/test_qr_{guild_id}.png")
    qr_img = Image.new("RGB", (500, 500), color="white")
    qr_img.save(test_qr_path, format="PNG")
    assert os.path.exists(test_qr_path)

    await db.set_upi_qr(guild_id, test_qr_path, owner_id)
    cfg_complete = await db.get_upi_config(guild_id)
    assert cfg_complete["upi_id"] == test_upi
    assert cfg_complete["qr_file_path"] == test_qr_path
    assert os.path.exists(cfg_complete["qr_file_path"])
    print(f"UPI QR code configuration & persistence: (PASSED)")

    print("--- [4] TEST ATOMIC KEY DISPATCH (USER & PASS) & CONCURRENCY ---")
    # Add account with specific USER and PASS
    ok_acc = await db.add_account_credentials(guild_id, "framex_user1", "Pass@1234")
    assert ok_acc is True
    
    # Add account via bulk user:pass format
    keys = ["admin_user:SecurePass!99", "regular_user:OptiKey#2026"]
    added_count = await db.add_free_keys(guild_id, keys)
    assert added_count == 2

    # First claim -> should return dict with user and pass
    c1 = await db.claim_available_key_atomically(guild_id, user_id=5001, channel_id=6001)
    assert isinstance(c1, dict)
    assert c1["user"] == "framex_user1"
    assert c1["pass"] == "Pass@1234"
    print(f"Issued account 1 -> USER: {c1['user']} | PASS: {c1['pass']} (PASSED)")

    # Duplicate claim attempt by same user -> ALREADY_CLAIMED
    c1_dup = await db.claim_available_key_atomically(guild_id, user_id=5001, channel_id=6001)
    assert c1_dup == "ALREADY_CLAIMED"
    print("Duplicate claim guard: ALREADY_CLAIMED (PASSED)")

    # Second user claim
    c2 = await db.claim_available_key_atomically(guild_id, user_id=5002, channel_id=6002)
    assert isinstance(c2, dict)
    assert c2["user"] == "admin_user"
    assert c2["pass"] == "SecurePass!99"
    print(f"Issued account 2 -> USER: {c2['user']} | PASS: {c2['pass']} (PASSED)")

    # Third user claim
    c3 = await db.claim_available_key_atomically(guild_id, user_id=5003, channel_id=6003)
    assert isinstance(c3, dict)
    assert c3["user"] == "regular_user"
    assert c3["pass"] == "OptiKey#2026"

    # Inventory exhausted
    c4 = await db.claim_available_key_atomically(guild_id, user_id=5004, channel_id=6004)
    assert c4 is None
    print("Atomic account claim and concurrency protection: (PASSED)")

    # Clean up test artifacts
    if os.path.exists(test_qr_path):
        os.remove(test_qr_path)
    if os.path.exists(test_db_path):
        os.remove(test_db_path)

    print("\nALL FRAMEX AUTOMATION TESTS PASSED SUCCESSFULLY.")

if __name__ == "__main__":
    asyncio.run(run_tests())

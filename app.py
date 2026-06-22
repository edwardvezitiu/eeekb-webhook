import os
import json
import hmac
import hashlib
import requests
from flask import Flask, request, jsonify
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

app = Flask(__name__)

# ── Rate limiting ─────────────────────────────────────────────────────────────
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://",
)

# ── Config ────────────────────────────────────────────────────────────────────
GROQ_API_KEY    = os.environ["GROQ_API_KEY"]
RESEND_API_KEY  = os.environ["RESEND_API_KEY"]
BUSINESS_EMAIL = "hello@eeekoreanbeauty.com"
BRAND_NAME      = "EEE Korean Beauty Ltd"
WEBHOOK_SECRET  = os.environ.get("WEBHOOK_SECRET", "")

# ── Groq ──────────────────────────────────────────────────────────────────────

def ask_groq(submission_text):
    prompt = f"""You are the customer support AI for {BRAND_NAME}, a Korean beauty brand.
A customer has submitted the following message via the website contact form:

---
{submission_text}
---

Respond ONLY with a valid JSON object — no preamble, no markdown, no backticks. Use this exact structure:

{{
  "category": "auto_reply" | "flag_only" | "general",
  "reason": "one sentence explaining your categorisation",
  "priority": "high" | "medium" | "low",
  "customer_reply": "your friendly, casual reply to the customer (null if category is flag_only)",
  "internal_summary": "a short summary for the business owner flagging what this is about"
}}

Rules:
- auto_reply: simple complaints, refund requests, order issues, basic product questions — AI handles it, but still flag to business
- flag_only: bugs, partnership enquiries, legal issues, media/press, anything requiring a human decision — do NOT auto-reply
- general: compliments, general feedback, surveys — log and flag lightly
- Tone for customer replies: friendly, warm, casual — represent {BRAND_NAME} well
- Always sign off customer replies as "The {BRAND_NAME} Team"
- Mark priority HIGH if it involves money, legal, urgent complaints, bugs affecting purchases, or partnership opportunities
"""

    r = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": "llama-3.1-8b-instant",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": 1000,
        },
        timeout=15,  # don't hang forever if Groq is slow
    )
    r.raise_for_status()
    raw = r.json()["choices"][0]["message"]["content"].strip()
    raw = raw.replace("```json", "").replace("```", "").strip()
    return json.loads(raw)

# ── Email ─────────────────────────────────────────────────────────────────────

def send_email(to, subject, html_body, reply_to=None):
    payload = {
       "from": f"{BRAND_NAME} <hello@eeekoreanbeauty.com>",
        "to": [to],
        "subject": subject,
        "html": html_body,
    }
    if reply_to:
        payload["reply_to"] = reply_to

    r = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=10,
    )
    r.raise_for_status()
    return r.json()

# ── Tally parser ──────────────────────────────────────────────────────────────

def parse_tally(payload):
    """Extract name, email and message from a Tally webhook payload."""
    fields = payload.get("data", {}).get("fields", [])
    data = {}
    for field in fields:
        label = field.get("label", "").lower().strip()
        value = field.get("value", "")
        if not value:
            continue
        if "email" in label:
            data["email"] = value
        elif label == "first name":
            data["first_name"] = value
        elif label == "last name":
            data["last_name"] = value
        elif "name" in label:
            data["first_name"] = value
        elif any(w in label for w in ["message", "comment", "feedback", "question", "enquiry"]):
            data["message"] = value
        else:
            data[label] = value
    return data

# ── Signature verification ────────────────────────────────────────────────────

def verify_tally_signature(request):
    """
    Verify the Tally webhook signature using HMAC-SHA256.
    Tally sends: X-Tally-Signature: sha256=<hex_digest>
    We compute HMAC of the raw request body with WEBHOOK_SECRET and compare.
    Returns True if valid (or if no secret is configured).
    """
    if not WEBHOOK_SECRET:
        return True  # no secret set, skip verification

    incoming = request.headers.get("X-Tally-Signature", "")
    if not incoming.startswith("sha256="):
        return False

    raw_body = request.get_data()
    expected = "sha256=" + hmac.new(
        WEBHOOK_SECRET.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()

    # constant-time compare to prevent timing attacks
    return hmac.compare_digest(incoming, expected)

# ── Webhook endpoint ──────────────────────────────────────────────────────────

@app.route("/webhook", methods=["POST"])
@limiter.limit("10 per minute; 100 per hour")  # per IP
def webhook():

    # ── Signature check ───────────────────────────────────────────────────────
    if not verify_tally_signature(request):
        print("❌ Signature verification failed")
        return jsonify({"error": "Unauthorized"}), 401

    # ── Parse payload ─────────────────────────────────────────────────────────
    try:
        payload = request.get_json(force=True)
        if not payload:
            return jsonify({"error": "Empty payload"}), 400
        print(f"DEBUG payload fields: {payload.get('data', {}).get('fields', [])}")
    except Exception:
        return jsonify({"error": "Invalid JSON"}), 400

    # ── Basic spam guard: reject if no fields at all ──────────────────────────
    fields = payload.get("data", {}).get("fields", [])
    if not fields:
        return jsonify({"status": "skipped", "reason": "no fields in payload"}), 200

    parsed  = parse_tally(payload)
    email   = parsed.get("email")
    name    = parsed.get("first_name", "there")

    lines = [f"{k.title()}: {v}" for k, v in parsed.items()]
    submission_text = "\n".join(lines)

    if not submission_text.strip():
        return jsonify({"status": "skipped", "reason": "empty submission"}), 200

    # ── Groq triage ───────────────────────────────────────────────────────────
    try:
        result = ask_groq(submission_text)
    except requests.exceptions.Timeout:
        print("❌ Groq timed out")
        # fail gracefully: flag to business without auto-reply
        result = {
            "category": "flag_only",
            "reason": "AI triage timed out — needs manual review",
            "priority": "medium",
            "customer_reply": None,
            "internal_summary": f"Triage failed (timeout). Original message: {submission_text[:300]}",
        }
    except Exception as e:
        print(f"❌ Groq error: {e}")
        result = {
            "category": "flag_only",
            "reason": "AI triage failed — needs manual review",
            "priority": "medium",
            "customer_reply": None,
            "internal_summary": f"Triage failed ({type(e).__name__}). Original message: {submission_text[:300]}",
        }

    category  = result.get("category", "general")
    priority  = result.get("priority", "low")
    reason    = result.get("reason", "")
    summary   = result.get("internal_summary", "")
    reply_txt = result.get("customer_reply")

    priority_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(priority, "⚪")

    # ── 1. Auto-reply to customer ─────────────────────────────────────────────
    if category == "auto_reply" and email and reply_txt:
        customer_html = f"""
<p>Hi {name},</p>
<p>{reply_txt.replace(chr(10), '</p><p>')}</p>
<br>
<p style="color:#888;font-size:12px;">
  This is an automated response to your recent enquiry. A member of our team is also being notified.
</p>
"""
        try:
            send_email(
                to=email,
                subject=f"Re: Your message to {BRAND_NAME}",
                html_body=customer_html,
            )
            print(f"✅ Auto-reply sent to {email}")
        except Exception as e:
            print(f"❌ Customer reply failed: {e}")

    # ── 2. Flag to business ───────────────────────────────────────────────────
    action_label = {
        "auto_reply": "Auto-replied to customer + flagged for your records",
        "flag_only":  "Needs your attention — no auto-reply sent",
        "general":    "Logged for your records",
    }.get(category, "Logged")

    reply_block = ""
    if reply_txt:
        reply_block = f"""
<hr>
<p><strong>Auto-reply sent to customer:</strong></p>
<blockquote style="border-left:3px solid #ccc;padding-left:12px;color:#555;">
  {reply_txt.replace(chr(10), '<br>')}
</blockquote>
"""

    internal_html = f"""
<h2 style="color:#333">{priority_emoji} New Feedback — {priority.upper()} priority</h2>

<table style="border-collapse:collapse;width:100%;font-family:sans-serif;font-size:14px">
  <tr><td style="padding:6px 12px;font-weight:bold;width:160px">Category</td><td style="padding:6px 12px">{category.replace('_',' ').title()}</td></tr>
  <tr style="background:#f9f9f9"><td style="padding:6px 12px;font-weight:bold">Priority</td><td style="padding:6px 12px">{priority_emoji} {priority.upper()}</td></tr>
  <tr><td style="padding:6px 12px;font-weight:bold">Action</td><td style="padding:6px 12px">{action_label}</td></tr>
  <tr style="background:#f9f9f9"><td style="padding:6px 12px;font-weight:bold">Customer</td><td style="padding:6px 12px">{name} — {email or 'no email provided'}</td></tr>
  <tr><td style="padding:6px 12px;font-weight:bold">AI summary</td><td style="padding:6px 12px">{summary}</td></tr>
  <tr style="background:#f9f9f9"><td style="padding:6px 12px;font-weight:bold">AI reason</td><td style="padding:6px 12px">{reason}</td></tr>
</table>

<hr>
<p><strong>Original message:</strong></p>
<blockquote style="border-left:3px solid #ccc;padding-left:12px;color:#555;">
  {submission_text.replace(chr(10), '<br>')}
</blockquote>

{reply_block}

<p style="color:#aaa;font-size:11px">Processed by EEE Korean Beauty AI feedback system</p>
"""

    subject = f"{priority_emoji} [{priority.upper()}] {BRAND_NAME} Feedback — {category.replace('_',' ').title()}"

    try:
        send_email(
            to=BUSINESS_EMAIL,
            subject=subject,
            html_body=internal_html,
            reply_to=email,
        )
        print(f"✅ Internal flag sent to {BUSINESS_EMAIL}")
    except Exception as e:
        print(f"❌ Internal flag failed: {e}")

    return jsonify({"status": "ok", "category": category, "priority": priority}), 200


@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "EEE Korean Beauty feedback processor is running 🌿"}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

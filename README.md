[README.md](https://github.com/user-attachments/files/28686714/README.md)
# EEE Korean Beauty — Feedback Webhook Server

Listens for Tally form submissions, triages them with Gemini AI, auto-replies to customers, and flags important messages to the business inbox.

---

## Deploy to Railway

### 1. Create a new GitHub repo
Upload these 3 files into a fresh repo (can be separate from your old one):
- `app.py`
- `requirements.txt`
- `Procfile`

### 2. Deploy on Railway
- Go to railway.app → **New Project → Deploy from GitHub repo**
- Select your repo — Railway auto-detects Python and deploys it
- Once deployed, Railway gives you a public URL like `https://your-app.up.railway.app`

### 3. Add environment variables on Railway
Go to your project → **Variables** tab and add:

| Variable | Value |
|---|---|
| `GEMINI_API_KEY` | Your Google AI Studio key |
| `RESEND_API_KEY` | `re_5YXxFN3R_...` |

### 4. Connect Tally webhook
- Go to your Tally form → **Integrations → Webhooks**
- Add your Railway URL + `/webhook` e.g: `https://your-app.up.railway.app/webhook`
- Save it

### 5. Test it
- Submit a test response on your Tally form
- Check your business email for the flag
- Check Railway logs (Deployments → View logs) if anything looks off

---

## Updating your business email
When `hello@eeekb.com` is confirmed, it's already set in `app.py` line 10. No changes needed unless it changes.

## Sending from your own domain
Once `eeekb.com` is verified on Resend, update line 57 in `app.py`:
```python
"from": f"{BRAND_NAME} <hello@eeekb.com>",
```

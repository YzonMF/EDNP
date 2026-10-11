# Keeping uploaded files in a Railway Storage Bucket

Uploaded documents and generated reports are files, not database rows: MySQL only holds each file's
name. Where the files themselves go is set by the `BUCKET_*` variables.

- **No bucket (the default):** files go to `MEDIA_ROOT` on the web service's own disk. That's fine on a
  laptop. On Railway, though, that disk is replaced at every redeploy, so the files are lost. The free
  plan allows one volume per project (0.5 GB, can't be resized), and MySQL already uses it.
- **With a bucket:** files go to a Railway Storage Bucket, which outlives redeploys. On the free plan a
  bucket can hold up to 10 GB, billed at $0.015 per GB per month out of the $1 monthly credit (1 GB of
  documents costs about $0.015 a month). If the credit runs out, the bucket is locked until the next
  billing cycle, but nothing is deleted.

The bucket is private. The site never links into it: every file still goes through the pages that
check who may see it, and with `DATA_ENCRYPTION_KEY` set the files in it are encrypted
(see encryption-at-rest.md).

## Setting it up (once)

1. **Create the bucket.** On the Railway project canvas: **Create → Bucket**. Pick the region nearest
   the Philippines (it can't be changed later) and give it a name such as `ednp-files`.
2. **Check the URL style.** Open the bucket's **Credentials** tab and note whether it says to use
   virtual-hosted or path-style URLs. New buckets use virtual-hosted, which is the site's default.
3. **Give the web service the bucket's details.** Open the web service → **Variables** → **Raw Editor**,
   and add these lines. Replace `ednp-files` with the bucket's name exactly as the canvas shows it;
   typing `${{` in a variable's value box lists the names you can use.

   ```
   BUCKET_NAME=${{ednp-files.BUCKET}}
   BUCKET_ENDPOINT=${{ednp-files.ENDPOINT}}
   BUCKET_REGION=${{ednp-files.REGION}}
   BUCKET_ACCESS_KEY_ID=${{ednp-files.ACCESS_KEY_ID}}
   BUCKET_SECRET_ACCESS_KEY=${{ednp-files.SECRET_ACCESS_KEY}}
   ```

   Only if step 2 said path-style, also add `BUCKET_URL_STYLE=path`.
4. **Deploy.** Variable changes are staged: press Railway's **Deploy** button to apply them (a plain
   Redeploy doesn't pick them up). The code that reads them must already be deployed, or be deployed
   in the same go. If a variable is missing, the site won't start and the deploy log names it
   (`ednp.E003`).
5. **Check it.** Upload a document, redeploy the web service, then open the document again. It should
   still be there.

## Files already on the server

- **If the web service has a volume** (web service → Settings → Volumes, mounted at `/data/media`), copy
  what's on it into the bucket once, after the deploy: run `railway ssh` into the web service, then
  `python manage.py copy_files_to_bucket --dry-run`, then the same without `--dry-run`. Files are copied
  exactly as stored (still encrypted if they were), files already in the bucket are skipped, and
  nothing on the volume is changed. Once every document opens, the volume can go.
- **If it has no volume** (the usual case on the free plan), there is nothing to copy: anything uploaded
  before the last redeploy is already gone and shows as "File missing". Anything uploaded since the last
  redeploy will be lost at this one, so download it before deploying and upload it again afterwards.

## Local development

Leave the `BUCKET_*` variables out of `.env` and files go to the `media/` folder as before. To try a
bucket locally, create a **separate** bucket for it and never point a laptop at the production one.

## What keeps storage small

- Photos and scans (JPG, PNG) up to 10 MB are accepted and shrunk before they are stored: turned the right
  way up, at most 2000 pixels on the longer side, saved as JPEG. That is usually 200–500 KB each.
- PDFs are stored as they are, so they are limited to 5 MB. Scanning in black and white, or at
  150–200 dpi, keeps them small.
- The documents archive (Backup Records) is built as it downloads and never kept on the server.
  Archives saved there before this change are listed under **Archives Saved on the Server**. Download
  any you still need, then delete them, because each one is a full copy of every document.
- Encryption adds about 0.1% to a file.
- Archived reports are deleted 30 days after archiving, or straight away with **Delete now** or
  **Clear all**.

As a rough guide, 200 clergy with 12 documents each comes to about 1 GB of documents once photos are
shrunk, against about 7 GB if they were kept as they came off a phone.

## The bucket is not a backup

It keeps files safe from redeploys, not from the account being closed or the credit running out. Every so
often, download both the **Database Backup** and the **Documents Archive** from Backup Records and keep
them somewhere off Railway. With encryption on, restoring them needs the same `DATA_ENCRYPTION_KEY`.

## When something is wrong

| Sign | Meaning |
| --- | --- |
| `ednp.E003` at startup | `BUCKET_NAME` is set but the message's other variables aren't. Add them (step 3). |
| `ednp.E004` at startup | `BUCKET_URL_STYLE` is something other than `virtual` or `path`. |
| `ednp.E005` at startup | The bucket libraries aren't installed: the deploy didn't install `requirements.txt`. |
| Uploads fail, with `SignatureDoesNotMatch`, `NoSuchBucket` or a connection error in the log | Check the variables are references to the right bucket. If its Credentials tab says path-style, set `BUCKET_URL_STYLE=path`. |
| Every document suddenly fails to open | The month's free credit has run out, so the bucket is locked until the next billing cycle. Nothing is deleted. |
| `manage.py check --deploy` shows `ednp.W002` | No bucket is set, so files are on the server's own disk. |

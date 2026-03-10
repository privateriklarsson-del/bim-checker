# JM BIM Checker

A lightweight IFC validation tool for early-stage model quality checks. Designers upload IFC files and validate them against JM's BIM requirements defined as IDS (Information Delivery Specification) files.

## Project Structure

```
bim-checker/
├── app.py                  # Main Streamlit application
├── requirements.txt        # Python dependencies
├── .streamlit/
│   └── config.toml         # Theme and server config
├── ids_files/              # IDS rule files (add your own here)
│   ├── A_basic_quality.ids
│   └── A_property_requirements.ids
└── README.md
```

## Adding Your Own Rules

Place `.ids` files in the `ids_files/` folder. The app auto-discovers them on startup. Name files with a prefix to control sort order (e.g., `A_`, `B_`, `K_` for discipline codes).

## Default Password

The prototype uses a simple password: `jm2025`. Change it in `app.py` before deploying.

---

## Deployment Guide: GitHub + Streamlit Cloud

### Step 1: Create a GitHub Account

1. Go to [github.com](https://github.com) and click **Sign up**
2. Use your personal email (not JM email — this is a prototype)
3. Choose a username and complete registration

### Step 2: Create a Repository

1. Once logged in, click the **+** button (top right) → **New repository**
2. Repository name: `bim-checker`
3. Set to **Private** (important — keeps your rules internal)
4. Check **"Add a README file"** — NO, leave unchecked (we have our own)
5. Click **Create repository**

### Step 3: Upload Files

1. On your new repo page, click **"uploading an existing file"** (or the **Add file** → **Upload files** button)
2. Drag and drop ALL files from this project:
   - `app.py`
   - `requirements.txt`
   - `README.md`
   - `.streamlit/config.toml` (you may need to show hidden files on your computer)
   - `ids_files/A_basic_quality.ids`
   - `ids_files/A_property_requirements.ids`
3. Important: maintain the folder structure. GitHub will auto-create folders.
4. Click **Commit changes**

**Tip for folder structure:** You may need to upload files in batches. First upload the root files, then create the `ids_files` folder (Add file → Create new file → type `ids_files/A_basic_quality.ids` and paste content).

### Step 4: Connect Streamlit Cloud

1. Go to [streamlit.io/cloud](https://streamlit.io/cloud)
2. Click **Sign up** → **Continue with GitHub**
3. Authorize Streamlit to access your GitHub
4. Click **New app**
5. Select your repository: `bim-checker`
6. Branch: `main`
7. Main file path: `app.py`
8. Click **Deploy**

The app will take a few minutes to build the first time (installing ifcopenshell is slow). After that, it's live at a URL like `https://your-username-bim-checker-app-xxxxx.streamlit.app`

### Step 5: Update the App

To update your app after deployment:
1. Go to your GitHub repo
2. Edit files directly on GitHub, or upload new versions
3. Streamlit Cloud auto-redeploys on every change to `main`

To add new IDS rules:
1. Go to `ids_files/` in your GitHub repo
2. Click **Add file** → **Upload files**
3. Upload your new `.ids` files
4. Commit — the app will auto-redeploy

---

## Running Locally (Alternative)

```bash
pip install -r requirements.txt
streamlit run app.py
```

Opens at `http://localhost:8501`

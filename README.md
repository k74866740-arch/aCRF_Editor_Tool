# aCRF_Editor_Tool 🛠️

A dedicated XFDF annotation editing and copy-paste utility designed for Clinical Trial aCRF (Annotated Case Report Form) development.

## 📝 Overview
Developing an aCRF often involves tedious manual copying and adjustments of PDF annotations. This tool seamlessly integrates a Python backend with a web-based frontend interface, resolving the cumbersome workflows of managing rectangular coordinates (`Rect`) and updating unique XFDF identifiers. It significantly enhances the productivity of Statistical Programmers.

## ✨ Key Features
* **Visual Web Interface**: An intuitive HTML frontend that allows users to easily manage bookmarks and search results.
* **Smart Copy & Paste (Ctrl+C / Ctrl+V)**: Automatically generates standardized uppercase UUIDs conforming to PDF specifications, enabling rapid duplication and multiple placements of annotations.
* **Precision Backend Modification**: The Python backend utilizes highly efficient Regular Expressions (Regex) to target and replace only the outermost `name` attribute of the `<freetext>` tags without breaking the underlying rich text structure.
* **Robust JSON/XFDF Parsing**: Efficiently parses rectangular coordinates (`Rect`) from XFDF files with built-in styling, fonts, and color fallback protections.

## 👤 Author
* **Created by**: Chris Peng
* **Development Year**: 2026
* **Contact**: [GitHub Profile](https://github.com/k74866740-arch) | [Project Repository](https://github.com)
* **Email**: k74866740@gmail.com

> 💡 **Copyright Notice**: This project is independently developed by Chris Peng. Community discussions, contributions, and usage are highly welcomed. Please ensure this attribution remains intact in any derived developments.

## 🚀 Quick Start

This project supports two environment setup methods depending on your company's IT security policy.

### Option A: Standard Modern Setup (Recommended)
If your machine allows running modern package managers, use **uv**:
1. Run `uv sync` in the terminal to set up the environment and dependencies automatically.
2. Run the tool using:
   ```bash
   uv run aCRF_Editor_Tool.py
   ```

### Option B: Built-in Python Setup (For Restricted IT Environments)
If your corporate machine restricts external installers (e.g., IT blocked `.msi` or `.exe`), use Python's built-in `venv` and `pip`:
1. Create a local virtual environment:
   ```powershell
   python -m venv .venv
   ```

2. Activate the virtual environment:
   ```powershell
   .\.venv\Scripts\Activate.ps1
   ```
   > 💡 **Windows Troubleshooting Tip:** If you see a red error saying *Execution of scripts is disabled on this system*, run the command below first, then try activating again:
   > ```powershell
   > Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process
   > ```

3. Automatically install all dependencies defined in `pyproject.toml`:
   ```powershell
   pip install .
   ```

4. Run the tool using:
   ```powershell
   python aCRF_Editor_Tool.py
   ```

## 📦 File Delivery & Deployment
Since **pdf.js** and **pdf.worker.js** are already bundled within this repository, you only need to ensure the following core components remain in the same directory:
* `aCRF_Editor_Tool.py` (Backend Engine)
* `pyproject.toml` (Dependency Specification)
* `pdf.js` & `pdf.worker.js` (Web Rendering Layer)

The system defaults to port **8080**. Once running, simply open `http://127.0.0.1:8080` in your web browser, load your files, and enjoy a seamless annotation editing experience!

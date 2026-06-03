# aCRF_Editor_Tool 🛠️

A dedicated XFDF annotation editing and copy-paste utility designed for Clinical Trial aCRF (Annotated Case Report Form) development.

## 📝 Overview
Developing an aCRF often involves tedious manual copying and adjustments of PDF annotations. This tool seamlessly integrates a Python backend with a web-based frontend interface, resolving the cumbersome workflows of managing rectangular coordinates (`Rect`) and updating unique XFDF identifiers (`Name`/`UUID`). It significantly enhances the productivity of Statistical Programmers.

## ✨ Key Features
* **Visual Web Interface**: An intuitive HTML frontend that allows users to easily manage bookmarks and search results.
* **Smart Copy & Paste (Ctrl+C / Ctrl+V)**: Automatically generates standardized uppercase UUIDs conforming to PDF specifications, enabling rapid duplication and multiple placements of annotations.
* **Precision Backend Modification**: The Python backend utilizes highly efficient Regular Expressions (Regex) to target and replace only the outermost `name` attribute of the `<freetext>` tags without breaking the underlying rich text structure.
* **Format Resilience**: Robustly parses rectangular coordinates separated by either commas `,` or semicolons `;`, equipped with fallbacks for styles and colors.

## 👤 Author
* **Created by**: Chris Peng
* **Development Year**: 2026
* **Contact**: [GitHub Profile](https://github.com/k74866740-arch) | [Project Repository](https://github.com)
* **Email**: k74866740@gmail.com

> 💡 **Copyright Notice**: This project is independently developed by Chris Peng. Community discussions, contributions, and usage are highly welcomed. Please ensure this attribution remains intact in any derived developments.

## 🚀 Quick Start

### 1. Prerequisites
* Python 3.x
* A modern web browser (Chrome / Edge / Firefox)

### 2. Usage
1. Download both the Python script and the HTML file into the same directory.
2. Run the Python script to spin up the backend service.
3. Open the frontend interface in your browser, load your XFDF files, and enjoy a seamless annotation editing experience!

### 3. Additional Dependency Setup
This tool requires **pdf.js** and **pdf.worker.js** to render PDF files. 
Please ensure you place these two files into the same directory as the HTML file. You can download them from the official repository or use a CDN version.

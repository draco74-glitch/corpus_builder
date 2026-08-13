# Contributing to CorpusBuilder

Thank you for your interest in the CorpusBuilder project! All contributions are welcome.

## 📋 Table of Contents
- [How to Contribute](#how-to-contribute)
- [Report a Bug](#report-a-bug)
- [Suggest an Enhancement](#suggest-an-enhancement)
- [Development](#development)
- [Code Style](#code-style)
- [Testing](#testing)
- [License](#license)

## How to Contribute

1. **Fork** the repository
2. Create a feature branch: `git checkout -b feature/amazing-feature`
3. Make changes and commit: `git commit -m 'Add amazing feature'`
4. Push the branch: `git push origin feature/amazing-feature`
5. Open a **Pull Request**

## Report a Bug

Open an [Issue](https://github.com/draco74-glitch/corpus_builder/issues/new) and describe:

- **Python version** (3.13 or other)
- **OS** (Windows 10/11, Linux, macOS)
- **How you ran it** (CLI / GUI / built .exe)
- **Full traceback** of the error
- **Steps to reproduce**

## Suggest an Enhancement

Open an [Issue](https://github.com/draco74-glitch/corpus_builder/issues/new) with the `enhancement` label and describe:

- What problem your suggestion solves
- How you envision the solution
- Alternatives you've considered

## Development

### Setting Up Dev Environment

```bash
git clone https://github.com/draco74-glitch/corpus_builder.git
cd corpus_builder
python3.13 -m venv .venv
source .venv/bin/activate    # Linux/macOS
.venv\Scripts\activate       # Windows

pip install -r requirements.txt
pip install -e .[dev,gui,build]
```

### Project Structure

```
corpus_builder/
├── corpus_builder/           # Python package
│   ├── crawlers/             # Crawlers (HTML, PDF, GitHub, SE, academic)
│   ├── postproc/             # Post-processing (dedup, quality, normalize)
│   ├── gui.py                # Main GUI window
│   ├── config_generator.py   # config.yaml generator
│   ├── pipeline.py           # Orchestrator
│   └── ...
├── tests/                    # Unit tests (194+ tests)
├── CorpusBuilder.spec        # PyInstaller one-dir spec
├── build.bat / build.sh      # Build scripts
└── README.md
```

## Code Style

- Python 3.10+ (type hints, `match/case`, `|` instead of `Union`)
- `ruff` for linting: `ruff check corpus_builder/`
- Maximum line length: 100 characters
- Docstrings in Russian (consistent with existing code)
- Function names: `snake_case`
- Class names: `PascalCase`

## Testing

```bash
# Run all tests
pytest tests/ -v

# Run a specific module
pytest tests/test_quality_filters.py -v

# With coverage
pytest tests/ --cov=corpus_builder --cov-report=html
```

**Before submitting a PR, ensure all tests pass:**
```bash
pytest tests/ -q --ignore=tests/test_vcr_cassettes.py
```

## License

By contributing to this project, you agree that your code will be distributed
under the project's [non-commercial license](LICENSE). Any commercial use
requires separate permission from the author.

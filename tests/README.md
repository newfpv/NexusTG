Run the regression suite from the repository root after installing the project dependencies:

```sh
python -m unittest discover -s tests -v
```

The suite creates a temporary SQLite database and mocks Telegram and AI requests. It does not open the production database or send messages.

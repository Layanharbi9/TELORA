# TELORA tests.
import os

# The project .env turns LangSmith tracing on for the app. Tests must never send traces:
# they would mix test runs into the admin's Speed and cost numbers and use up the
# LangSmith trace quota. Tests that exercise tracing patch it explicitly.
os.environ["LANGSMITH_TRACING"] = "false"

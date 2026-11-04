FROM python:3.12-slim
WORKDIR /app
COPY terms_navigator ./terms_navigator
COPY documents ./documents
EXPOSE 8000
CMD ["python", "-m", "terms_navigator", "serve", "--host", "0.0.0.0", "--port", "8000"]

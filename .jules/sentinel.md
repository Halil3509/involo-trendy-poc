## 2025-05-18 - Sanitization of Brand PDF Export Error Responses
**Vulnerability:** Raw `BrandPdfProviderError` exception strings were returned directly in HTTP 502 responses (`str(exc)`), exposing internal AWS S3 bucket names, endpoint URLs, or system paths to API clients.
**Learning:** Provider exceptions can wrap lower-level cloud SDK (e.g., `boto3`) or system process (e.g., Playwright) errors that contain sensitive infrastructure paths and configuration parameters.
**Prevention:** Catch provider exceptions in FastAPI endpoints, log full details server-side using `logger.error(...)`, and return a sanitized, generic error message (e.g. `"Failed to generate brand analysis PDF"`) in `HTTPException`.

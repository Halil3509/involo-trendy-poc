"""Brand analysis markdown-to-PDF export provider."""

from __future__ import annotations

import asyncio
import os
import shutil
from abc import ABC, abstractmethod
from typing import Any, cast

import markdown

from app.core.config import Settings
from app.schemas.brand_analysis import BrandAnalysisPdf


class BrandPdfProviderError(RuntimeError):
    pass


class BrandAnalysisPdfProvider(ABC):
    @abstractmethod
    async def export(
        self,
        job_id: str,
        markdown_text: str,
        target_username: str,
    ) -> BrandAnalysisPdf:
        raise NotImplementedError

    @abstractmethod
    async def download(self, s3_key: str) -> bytes:
        raise NotImplementedError


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<style>
@page {{ size: A4; margin: 1.5cm; }}
body {{
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "DejaVu Sans", sans-serif;
  font-size: 11pt;
  line-height: 1.6;
  color: #1f2937;
}}
h1 {{
  font-size: 20pt;
  margin-bottom: 0.4cm;
  border-bottom: 2px solid #6366f1;
  padding-bottom: 0.2cm;
}}
h2 {{
  font-size: 14pt;
  margin-top: 0.8cm;
  margin-bottom: 0.3cm;
  color: #1e293b;
}}
h3 {{
  font-size: 12pt;
  margin-top: 0.6cm;
  margin-bottom: 0.2cm;
}}
table {{
  border-collapse: collapse;
  width: 100%;
  margin-top: 0.3cm;
  margin-bottom: 0.3cm;
}}
th, td {{
  border: 1px solid #cbd5e1;
  padding: 4pt 6pt;
  text-align: left;
  vertical-align: top;
}}
th {{
  background-color: #f1f5f9;
  font-weight: 600;
}}
ul, ol {{
  padding-left: 1.2cm;
}}
pre {{
  background: #f8fafc;
  padding: 8pt;
  border-radius: 4pt;
  overflow-x: auto;
  font-size: 10pt;
}}
code {{
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 0.9em;
  background: #f1f5f9;
  padding: 1pt 3pt;
  border-radius: 3pt;
}}
blockquote {{
  border-left: 3px solid #6366f1;
  margin: 0;
  padding-left: 10pt;
  color: #475569;
  font-style: italic;
}}
img {{
  max-width: 100%;
  height: auto;
  margin-top: 0.3cm;
  margin-bottom: 0.3cm;
}}
</style>
</head>
<body>{body}</body>
</html>"""


def _markdown_to_html(markdown_text: str) -> str:
    extensions = ["tables", "fenced_code"]
    body = markdown.markdown(markdown_text, extensions=extensions)
    return _HTML_TEMPLATE.format(body=body)


class PlaywrightBrandAnalysisPdfProvider(BrandAnalysisPdfProvider):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _s3_client(self) -> Any:
        import boto3

        options: dict[str, Any] = {"region_name": self.settings.media_s3_region}
        if self.settings.transcribe_s3_endpoint_url:
            options["endpoint_url"] = self.settings.transcribe_s3_endpoint_url
        if (
            self.settings.transcribe_s3_endpoint_url
            and self.settings.transcribe_s3_access_key_id
        ):
            options["aws_access_key_id"] = self.settings.transcribe_s3_access_key_id
        if (
            self.settings.transcribe_s3_endpoint_url
            and self.settings.transcribe_s3_secret_access_key
        ):
            options["aws_secret_access_key"] = (
                self.settings.transcribe_s3_secret_access_key.get_secret_value()
            )
        return boto3.client("s3", **options)

    async def export(
        self,
        job_id: str,
        markdown_text: str,
        target_username: str,
    ) -> BrandAnalysisPdf:
        try:
            html = _markdown_to_html(markdown_text)
        except Exception as exc:
            raise BrandPdfProviderError(f"Failed to convert markdown to HTML: {exc}") from exc

        try:
            pdf_bytes = await self._render_pdf(html)
        except Exception as exc:
            raise BrandPdfProviderError(f"Failed to render PDF: {exc}") from exc

        key = f"reports/brand/{job_id}/report.pdf"
        await self._upload_pdf(key, pdf_bytes)
        return BrandAnalysisPdf(job_id=job_id, pdf_bytes=pdf_bytes, pdf_s3_key=key)

    @staticmethod
    def _resolve_chromium_executable(playwright: Any) -> str | None:
        """Return the bundled Chromium if present, otherwise a system Chrome/Chromium."""
        bundled = playwright.chromium.executable_path
        if bundled and os.path.exists(bundled):
            return str(bundled)
        for name in ("google-chrome", "chromium", "chromium-browser", "google-chrome-stable"):
            path = shutil.which(name)
            if path:
                return path
        return None

    async def _render_pdf(self, html: str) -> bytes:
        from playwright.async_api import async_playwright

        timeout = self.settings.brand_analysis_pdf_timeout_seconds * 1_000
        try:
            async with async_playwright() as playwright:
                executable_path = self._resolve_chromium_executable(playwright)
                launch_kwargs: dict[str, Any] = {
                    "headless": True,
                    "args": ["--no-sandbox"],
                }
                if executable_path:
                    launch_kwargs["executable_path"] = executable_path
                browser = await playwright.chromium.launch(**launch_kwargs)
                try:
                    page = await browser.new_page()
                    await page.set_content(html, timeout=timeout)
                    pdf_bytes = await page.pdf(
                        format="A4",
                        margin={
                            "top": "1.5cm",
                            "right": "1cm",
                            "bottom": "1cm",
                            "left": "1cm",
                        },
                    )
                finally:
                    await browser.close()
            return pdf_bytes
        except Exception as exc:
            raise BrandPdfProviderError(f"Playwright PDF rendering failed: {exc}") from exc

    async def _upload_pdf(self, key: str, pdf_bytes: bytes) -> None:
        bucket = self.settings.media_s3_bucket
        if not bucket:
            raise BrandPdfProviderError("media S3 bucket is not configured for PDF storage")

        def _put() -> None:
            s3 = self._s3_client()
            s3.put_object(
                Bucket=bucket,
                Key=key,
                Body=pdf_bytes,
                ContentType="application/pdf",
            )

        try:
            await asyncio.to_thread(_put)
        except Exception as exc:
            raise BrandPdfProviderError(f"Failed to upload PDF to S3: {exc}") from exc

    async def download(self, s3_key: str) -> bytes:
        bucket = self.settings.media_s3_bucket
        if not bucket:
            raise BrandPdfProviderError("media S3 bucket is not configured for PDF storage")

        def _get() -> bytes:
            s3 = self._s3_client()
            response = s3.get_object(Bucket=bucket, Key=s3_key)
            return cast(bytes, response["Body"].read())

        try:
            return await asyncio.to_thread(_get)
        except Exception as exc:
            raise BrandPdfProviderError(f"Failed to download PDF from S3: {exc}") from exc


class FakeBrandAnalysisPdfProvider(BrandAnalysisPdfProvider):
    async def export(
        self,
        job_id: str,
        markdown_text: str,
        target_username: str,
    ) -> BrandAnalysisPdf:
        return BrandAnalysisPdf(
            job_id=job_id,
            pdf_bytes=b"%PDF-1.4 fake pdf content",
            pdf_s3_key=f"reports/brand/{job_id}/report.pdf",
        )

    async def download(self, s3_key: str) -> bytes:
        return b"%PDF-1.4 fake pdf content"


def build_brand_analysis_pdf_provider(settings: Settings) -> BrandAnalysisPdfProvider:
    if settings.brand_analysis_pdf_provider == "fake":
        return FakeBrandAnalysisPdfProvider()
    if settings.brand_analysis_pdf_provider == "playwright":
        return PlaywrightBrandAnalysisPdfProvider(settings)
    raise BrandPdfProviderError(
        f"unknown brand_analysis_pdf_provider: {settings.brand_analysis_pdf_provider}"
    )

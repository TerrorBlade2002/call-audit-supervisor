"""HTML → PDF rendering for the downloadable report artifact (FR11).

Uses headless Chromium (Playwright) so the PDF is a pixel-faithful render of the exact
report HTML — same template, no CSS-fidelity loss. Kept isolated and lazy: Chromium is only
launched when a PDF is actually requested, and the import is deferred so the rest of the API
runs fine on hosts where the browser isn't installed (the endpoint then returns 503).

Deploy note: the runtime image needs Chromium — `python -m playwright install --with-deps
chromium` (one line in Dockerfile.api).
"""

from __future__ import annotations

import structlog

log = structlog.get_logger("reports.pdf")


class PdfUnavailable(RuntimeError):
    """Raised when Chromium/Playwright isn't available to render a PDF."""


async def html_to_pdf(html: str) -> bytes:
    """Render a full HTML document to PDF bytes. Raises PdfUnavailable if Chromium is missing."""
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:  # pragma: no cover - depends on install
        raise PdfUnavailable("playwright is not installed") from exc

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
            try:
                page = await browser.new_page()
                # Keep the screen (dark) styling rather than the print fallback, matching the
                # report template; render fonts/background faithfully.
                await page.emulate_media(media="screen")
                await page.set_content(html, wait_until="load")
                await page.wait_for_timeout(250)  # let webfonts settle
                return await page.pdf(
                    print_background=True,
                    prefer_css_page_size=True,
                    margin={"top": "12mm", "bottom": "12mm", "left": "10mm", "right": "10mm"},
                )
            finally:
                await browser.close()
    except PdfUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - browser launch can fail many ways
        log.warning("pdf.render_failed", error=str(exc)[:160])
        raise PdfUnavailable(str(exc)) from exc

import logging
import re
# The process uses a fixed argv, shell=False and the current trusted interpreter.
import subprocess  # nosec B404
import sys
import time
import unicodedata
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlencode, urlparse

import pandas as pd
from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from selenium import webdriver
from selenium.common.exceptions import NoSuchWindowException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait

from comparator.models import (
    BaseUnit,
    MetroOffer,
    MetroOfferTier,
    MetroProductState,
    MetroScrapeJob,
    MetroScrapedProduct,
    MetroScrapeTerm,
    Product,
    ProductCode,
)

from .matching import suggest_product


logger = logging.getLogger(__name__)


def metro_offer_source(store_name):
    return f"Selenium {store_name or 'METRO'}"[:120]


def update_metro_product_state(row, product):
    store_name = (row.store_name or row.job.store_name or "METRO").strip()[:120]
    state = MetroProductState.objects.select_for_update().filter(
        store_name=store_name,
        external_id=row.external_id,
    ).first()
    if state is None:
        return MetroProductState.objects.create(
            product=product,
            external_id=row.external_id,
            store_name=store_name,
            first_seen_at=row.captured_at,
            last_seen_at=row.captured_at,
            first_seen_job=row.job,
            last_seen_job=row.job,
            last_price_gross=row.price_gross,
            last_volume_prices=row.volume_prices,
            last_units_per_package=row.units_per_package,
            last_unit_size=row.unit_size,
            last_base_unit=row.base_unit,
            last_package_text=row.package_text,
        )
    was_unavailable = not state.available
    price_changed = (
        state.last_price_gross != row.price_gross
        or state.last_volume_prices != row.volume_prices
    )
    package_changed = (
        state.last_units_per_package != row.units_per_package
        or state.last_unit_size != row.unit_size
        or state.last_base_unit != row.base_unit
    )
    state.product = product
    state.last_seen_at = row.captured_at
    state.last_seen_job = row.job
    state.reactivated_in_job = row.job if was_unavailable else state.reactivated_in_job
    state.consecutive_misses = 0
    state.available = True
    state.last_price_gross = row.price_gross
    state.last_volume_prices = row.volume_prices
    state.last_units_per_package = row.units_per_package
    state.last_unit_size = row.unit_size
    state.last_base_unit = row.base_unit
    state.last_package_text = row.package_text
    state.save(
        update_fields=[
            "product",
            "last_seen_at",
            "last_seen_job",
            "reactivated_in_job",
            "consecutive_misses",
            "available",
            "last_price_gross",
            "last_volume_prices",
            "last_units_per_package",
            "last_unit_size",
            "last_base_unit",
            "last_package_text",
        ]
    )
    counters = {}
    if price_changed:
        counters["price_changes_count"] = F("price_changes_count") + 1
    if package_changed:
        counters["package_changes_count"] = F("package_changes_count") + 1
    if counters:
        MetroScrapeJob.objects.filter(pk=row.job_id).update(**counters)
    return state


@transaction.atomic
def finalize_catalog_job(job):
    job = MetroScrapeJob.objects.select_for_update().get(pk=job.pk)
    if job.lifecycle_finalized_at:
        return job
    complete = (
        job.status == MetroScrapeJob.Status.COMPLETED
        and job.total_queries > 0
        and job.completed_queries == job.total_queries
        and not job.terms.exclude(status=MetroScrapeTerm.Status.COMPLETED).exists()
        and job.products.exists()
        and not job.products.filter(imported=False).exists()
        and bool(job.store_name.strip())
    )
    if not complete:
        return job

    missing_states = list(
        MetroProductState.objects.select_for_update()
        .filter(store_name=job.store_name, available=True)
        .exclude(last_seen_job=job)
    )
    newly_unavailable_product_ids = []
    for state in missing_states:
        state.consecutive_misses += 1
        if state.consecutive_misses >= 2:
            state.available = False
            newly_unavailable_product_ids.append(state.product_id)
        state.save(update_fields=["consecutive_misses", "available"])

    if newly_unavailable_product_ids:
        MetroOffer.objects.filter(
            product_id__in=newly_unavailable_product_ids,
            source=metro_offer_source(job.store_name),
            active=True,
        ).update(active=False)

    job.new_products_count = MetroProductState.objects.filter(first_seen_job=job).count()
    job.reactivated_products_count = MetroProductState.objects.filter(reactivated_in_job=job).count()
    job.missing_products_count = len(missing_states)
    job.unavailable_products_count = len(newly_unavailable_product_ids)
    job.lifecycle_finalized_at = timezone.now()
    job.save(
        update_fields=[
            "new_products_count",
            "reactivated_products_count",
            "missing_products_count",
            "unavailable_products_count",
            "lifecycle_finalized_at",
        ]
    )
    return job


CARD_DATA_SCRIPT = """
return Array.from(document.querySelectorAll('.sd-articlecard')).map(card => {
  const link = card.querySelector('a.title[href*="/shop/pv/"]');
  const title = card.querySelector('a.title h4');
  const packageNode = card.querySelector('.bundle.packaging-type');
  const storeNode = card.querySelector('.availability-details-inner');
  const priceNode = card.querySelector('.price-display-main-row .primary');
  if (!link || !title || !priceNode) return null;
  return {
    name: title.textContent.trim(),
    product_url: link.href,
    package_text: packageNode ? packageNode.textContent.trim() : '',
    store_text: storeNode ? storeNode.textContent.trim() : '',
    price_text: priceNode.textContent.trim(),
    volume_price_texts: Array.from(card.querySelectorAll('.bulk-discount-label'))
      .map(node => node.textContent.trim())
  };
}).filter(Boolean);
"""


OVERLAY_SCRIPT = """
let box = document.getElementById('__pricematch_metro_helper');
if (!box) {
  box = document.createElement('div');
  box.id = '__pricematch_metro_helper';
  Object.assign(box.style, {
    position: 'fixed', right: '18px', bottom: '18px', zIndex: '2147483647',
    width: '330px', padding: '16px', borderRadius: '12px', color: '#fff',
    background: '#173f31', boxShadow: '0 12px 40px rgba(0,0,0,.35)',
    font: '14px system-ui, sans-serif'
  });
  const title = document.createElement('strong');
  title.textContent = 'PriceMatch · scanare METRO';
  title.style.display = 'block';
  title.style.fontSize = '16px';
  const help = document.createElement('p');
  help.textContent = 'Alege magazinul, caută sau deschide o categorie, apoi capturează produsele vizibile.';
  help.style.lineHeight = '1.4';
  help.style.margin = '8px 0';
  const status = document.createElement('div');
  status.id = '__pricematch_status';
  status.style.marginBottom = '10px';
  const capture = document.createElement('button');
  capture.textContent = 'Capturează pagina';
  capture.style.cssText = 'padding:9px 11px;border:0;border-radius:7px;margin-right:7px;cursor:pointer;font-weight:700';
  capture.onclick = () => document.documentElement.setAttribute('data-pricematch-action', 'capture');
  const watchlist = document.createElement('button');
  watchlist.textContent = 'Actualizează lista urmărită';
  watchlist.style.cssText = 'display:block;width:100%;padding:9px 11px;border:0;border-radius:7px;margin:8px 0;cursor:pointer;font-weight:700;background:#dcecdf;color:#173f31';
  watchlist.onclick = () => document.documentElement.setAttribute('data-pricematch-action', 'watchlist');
  const finish = document.createElement('button');
  finish.textContent = 'Finalizează';
  finish.style.cssText = 'padding:9px 11px;border:1px solid #fff;border-radius:7px;background:transparent;color:#fff;cursor:pointer;font-weight:700';
  finish.onclick = () => document.documentElement.setAttribute('data-pricematch-action', 'finish');
  box.append(title, help, status, capture, watchlist, finish);
  document.body.appendChild(box);
}
const status = document.getElementById('__pricematch_status');
if (status) status.textContent = arguments[0] + ' produse unice capturate';
"""


def _decimal(value):
    try:
        return Decimal(str(value).replace(" ", "").replace(",", "."))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _price_from_text(text):
    matches = re.findall(r"(\d+(?:[.,]\d+)?)\s*(?:RON|LEI)", text or "", flags=re.IGNORECASE)
    return _decimal(matches[-1]) if matches else None


def _volume_prices_from_texts(texts):
    tiers = {}
    if not isinstance(texts, (list, tuple)):
        return []
    for raw_text in texts:
        label = " ".join(str(raw_text or "").split())[:120]
        match = re.search(
            r"(\d+(?:[.,]\d+)?)\s*(?:RON|LEI).*?pentru\s+(\d+)\s*\+",
            label,
            flags=re.IGNORECASE,
        )
        if not match:
            continue
        price = _decimal(match.group(1))
        min_packages = int(match.group(2))
        if price is None or min_packages < 2:
            continue
        tiers[min_packages] = {
            "min_packages": min_packages,
            "price_gross": f"{price:.2f}",
            "label": label,
        }
    return [tiers[key] for key in sorted(tiers)]


def _store_from_text(text):
    match = re.search(r"\bMETRO\s+(.+)$", " ".join((text or "").split()), flags=re.IGNORECASE)
    return f"METRO {match.group(1).strip()}"[:120] if match else ""


def _external_id(url):
    match = re.search(r"/shop/pv/([^/]+)", url or "")
    if match:
        return match.group(1)[:80]
    return urlparse(url or "").path.rstrip("/").split("/")[-1][:80]


def _convert_size(size, unit):
    value = _decimal(size) or Decimal("1")
    unit = unit.lower()
    if unit in {"g", "gr"}:
        return value / 1000, BaseUnit.KILOGRAM
    if unit == "kg":
        return value, BaseUnit.KILOGRAM
    if unit == "ml":
        return value / 1000, BaseUnit.LITER
    return value, BaseUnit.LITER


def parse_measurement(name, package_text=""):
    package = " ".join((package_text or "").upper().split())
    if re.search(r"\b1\s+KILOGRAM\b", package):
        return Decimal("1"), Decimal("1"), BaseUnit.KILOGRAM
    if re.search(r"\b1\s+LITR(?:U|I)?\b", package):
        return Decimal("1"), Decimal("1"), BaseUnit.LITER

    normalized_name = (name or "").replace(",", ".")
    multi = re.search(
        r"(?i)\b(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)\s*(kg|gr?|g|ml|l)\b",
        normalized_name,
    )
    if multi:
        unit_size, base_unit = _convert_size(multi.group(2), multi.group(3))
        return _decimal(multi.group(1)) or Decimal("1"), unit_size, base_unit

    pieces = re.search(r"(?i)\b(\d+(?:\.\d+)?)\s*buc(?:ata|ati)?\b", normalized_name)
    if pieces:
        return _decimal(pieces.group(1)) or Decimal("1"), Decimal("1"), BaseUnit.PIECE

    sizes = list(re.finditer(r"(?i)\b(\d+(?:\.\d+)?)\s*(kg|gr?|g|ml|l)\b", normalized_name))
    if sizes:
        unit_size, base_unit = _convert_size(sizes[-1].group(1), sizes[-1].group(2))
        package_count = re.match(r"^\s*(\d+)\s*", package)
        units = _decimal(package_count.group(1)) if package_count else Decimal("1")
        return units or Decimal("1"), unit_size, base_unit
    return Decimal("1"), Decimal("1"), BaseUnit.PIECE


def normalize_dom_rows(raw_rows):
    if not raw_rows:
        return []
    frame = pd.DataFrame(raw_rows)
    frame = frame.dropna(subset=["name", "product_url", "price_text"])
    frame["external_id"] = frame["product_url"].map(_external_id)
    frame["price_gross"] = frame["price_text"].map(_price_from_text)
    frame["store_name"] = frame["store_text"].map(_store_from_text)
    frame = frame.dropna(subset=["price_gross"])
    frame = frame[frame["external_id"].astype(bool)].drop_duplicates(subset=["external_id"], keep="last")

    result = []
    for row in frame.to_dict(orient="records"):
        units, size, base_unit = parse_measurement(row["name"], row.get("package_text", ""))
        result.append(
            {
                "external_id": row["external_id"],
                "name": str(row["name"]).strip()[:240],
                "product_url": str(row["product_url"])[:1000],
                "store_name": str(row.get("store_name", ""))[:120],
                "package_text": str(row.get("package_text", ""))[:120],
                "units_per_package": units,
                "unit_size": size,
                "base_unit": base_unit,
                "price_gross": row["price_gross"],
                "volume_prices": _volume_prices_from_texts(row.get("volume_price_texts")),
            }
        )
    return result


def _load_all_visible_cards(driver):
    stable_rounds = 0
    previous = 0
    for _ in range(24):
        current = len(driver.find_elements(By.CSS_SELECTOR, ".sd-articlecard"))
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight)")
        try:
            WebDriverWait(driver, 1.5).until(
                lambda d: len(d.find_elements(By.CSS_SELECTOR, ".sd-articlecard")) > current
            )
        except Exception as exc:
            logger.debug("METRO infinite-scroll wait expired: %s", exc)
        updated = len(driver.find_elements(By.CSS_SELECTOR, ".sd-articlecard"))
        if updated <= previous:
            stable_rounds += 1
        else:
            stable_rounds = 0
        previous = updated
        if stable_rounds >= 2:
            break


def _displayed_search_input(driver):
    for element in driver.find_elements(By.ID, "global-header-search-input"):
        if element.is_displayed() and element.is_enabled():
            return element
    return None


def create_metro_driver(headless=False):
    profile = Path(settings.METRO_BROWSER_PROFILE)
    profile.mkdir(parents=True, exist_ok=True)
    options = webdriver.ChromeOptions()
    options.add_argument(f"--user-data-dir={profile}")
    options.add_argument("--profile-directory=Default")
    options.add_argument("--window-size=1440,1100")
    options.add_argument("--lang=ro-RO")
    options.add_argument("--disable-dev-shm-usage")
    if headless:
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
    return webdriver.Chrome(options=options)


def dismiss_cookie_banner(driver):
    return driver.execute_script(
        """
        const host = document.querySelector('cms-cookie-disclaimer');
        if (!host) return false;
        const root = host.shadowRoot || host;
        const button = Array.from(root.querySelectorAll('button'))
          .find(item => item.textContent.includes('Nu sunt de acord'));
        if (!button) return false;
        button.click();
        return true;
        """
    )


def _plain_text(value):
    return unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode("ascii").lower()


def select_metro_store(driver, store_query):
    """Select a METRO store using the site's own store picker and persistent browser profile."""
    toggle = WebDriverWait(driver, 20).until(
        lambda d: d.find_element(By.CSS_SELECTOR, ".brandbar-store-toggle")
    )
    driver.execute_script("arguments[0].click()", toggle)
    control = WebDriverWait(driver, 10).until(
        lambda d: d.find_element(By.CSS_SELECTOR, "#storesselector_stores .Select__control")
    )
    driver.execute_script("arguments[0].click()", control)
    search = WebDriverWait(driver, 10).until(
        lambda d: d.find_element(By.CSS_SELECTOR, "#storesselector_stores input")
    )
    search.send_keys(Keys.CONTROL, "a")
    search.send_keys(store_query)
    query_tokens = set(_plain_text(store_query).split())

    def matching_option(current_driver):
        for option in current_driver.find_elements(By.CSS_SELECTOR, "#storesselector_stores .Select__option"):
            if query_tokens.issubset(set(_plain_text(option.text).split())):
                return option
        return False

    option = WebDriverWait(driver, 15).until(matching_option)
    selected_name = option.text.strip()
    driver.execute_script("arguments[0].click()", option)
    confirm = WebDriverWait(driver, 10).until(
        lambda d: next(
            (
                button
                for button in d.find_elements(By.CSS_SELECTOR, ".sd-store-selector button")
                if "selecteaza magazin" in _plain_text(button.text)
            ),
            False,
        )
    )
    driver.execute_script("arguments[0].click()", confirm)
    WebDriverWait(driver, 20).until(
        lambda d: _plain_text(d.find_element(By.CSS_SELECTOR, ".brandbar-store-data-name").text)
        == _plain_text(selected_name)
    )
    return selected_name


def _set_overlay_status(driver, message):
    driver.execute_script(
        "const s=document.getElementById('__pricematch_status'); if(s) s.textContent=arguments[0];",
        message,
    )


def capture_watchlist(driver, job):
    terms = list(Product.objects.filter(active=True).order_by("name").values_list("name", flat=True)[:150])
    if not terms:
        _set_overlay_status(driver, "Catalogul urmărit este gol. Capturează mai întâi o categorie.")
        return 0
    for index, term in enumerate(terms, start=1):
        _set_overlay_status(driver, f"Caut {index}/{len(terms)}: {term[:38]}")
        search = _displayed_search_input(driver)
        if not search:
            raise RuntimeError("Bara de căutare METRO nu a fost găsită.")
        old_url = driver.current_url
        search.click()
        search.send_keys(Keys.CONTROL, "a")
        search.send_keys(term)
        search.send_keys(Keys.ENTER)
        try:
            WebDriverWait(driver, 15).until(lambda d: d.current_url != old_url)
        except Exception as exc:
            logger.debug("METRO search URL did not change before timeout: %s", exc)
        try:
            WebDriverWait(driver, 12).until(
                lambda d: len(d.find_elements(By.CSS_SELECTOR, ".sd-articlecard")) > 0
                or "niciun" in d.find_element(By.TAG_NAME, "body").text.lower()
            )
        except Exception as exc:
            logger.warning("METRO search skipped for %r: %s", term, exc)
            continue
        raw_rows = driver.execute_script(CARD_DATA_SCRIPT)[:8]
        store_captured_rows(job, normalize_dom_rows(raw_rows))
        time.sleep(1)
    _set_overlay_status(driver, f"Gata: {job.products.count()} produse unice capturate")
    return job.products.count()


def capture_search_terms(
    job,
    terms,
    limit_per_search=8,
    delay_seconds=1,
    headless=True,
    progress=None,
    store_query="",
    term_categories=None,
    retries=3,
    refresh_completed=False,
):
    """Capture search results with persistent per-term checkpoints and bounded retries."""
    term_categories = term_categories or {}
    for term in dict.fromkeys(terms):
        MetroScrapeTerm.objects.get_or_create(
            job=job,
            term=term,
            defaults={"category": term_categories.get(term, "")},
        )
    job.total_queries = job.terms.count()
    job.completed_queries = job.terms.filter(status=MetroScrapeTerm.Status.COMPLETED).count()
    job.save(update_fields=["total_queries", "completed_queries"])

    driver = create_metro_driver(headless=headless)
    try:
        if store_query:
            driver.get(job.start_url)
            WebDriverWait(driver, 20).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
            dismiss_cookie_banner(driver)
            selected_store = select_metro_store(driver, store_query)
            job.store_name = selected_store[:120]
            job.save(update_fields=["store_name"])
            if progress:
                progress(job.completed_queries, job.total_queries, f"Magazin: {selected_store}", job.captured_count)
        origin = urlparse(job.start_url)
        search_base = f"{origin.scheme}://{origin.netloc}/shop/search"
        term_rows = job.terms.all()
        if not refresh_completed:
            term_rows = term_rows.exclude(status=MetroScrapeTerm.Status.COMPLETED)
        for term_row in term_rows:
            last_error = None
            for attempt in range(max(1, retries)):
                term_row.status = MetroScrapeTerm.Status.RUNNING
                term_row.attempts += 1
                term_row.started_at = timezone.now()
                term_row.error = ""
                term_row.save(update_fields=["status", "attempts", "started_at", "error"])
                try:
                    driver.get(f"{search_base}?{urlencode({'q': term_row.term})}")
                    WebDriverWait(driver, 20).until(
                        lambda d: d.execute_script("return document.readyState") == "complete"
                    )
                    dismiss_cookie_banner(driver)
                    WebDriverWait(driver, 20).until(
                        lambda d: len(d.find_elements(By.CSS_SELECTOR, ".sd-articlecard")) > 0
                        or "nu am gasit" in _plain_text(d.find_element(By.TAG_NAME, "body").text)
                    )
                    _load_all_visible_cards(driver)
                    raw_rows = driver.execute_script(CARD_DATA_SCRIPT)
                    if limit_per_search:
                        raw_rows = raw_rows[:limit_per_search]
                    normalized_rows = normalize_dom_rows(raw_rows)
                    for row in normalized_rows:
                        row["category"] = term_row.category
                    store_captured_rows(job, normalized_rows)
                    term_row.status = MetroScrapeTerm.Status.COMPLETED
                    term_row.found_count = len(normalized_rows)
                    term_row.finished_at = timezone.now()
                    term_row.error = ""
                    term_row.save(
                        update_fields=["status", "found_count", "finished_at", "error"]
                    )
                    break
                except (WebDriverException, TimeoutError) as exc:
                    last_error = exc
                    term_row.status = MetroScrapeTerm.Status.ERROR
                    term_row.error = str(exc)[:2000]
                    term_row.finished_at = timezone.now()
                    term_row.save(update_fields=["status", "error", "finished_at"])
                    if attempt + 1 < max(1, retries):
                        time.sleep(min(2 ** attempt, 8))
            job.current_url = driver.current_url[:1000]
            job.completed_queries = job.terms.filter(status=MetroScrapeTerm.Status.COMPLETED).count()
            job.save(update_fields=["current_url", "completed_queries"])
            if progress:
                label = term_row.term if not last_error or term_row.status == MetroScrapeTerm.Status.COMPLETED else f"{term_row.term} (eroare)"
                progress(job.completed_queries, job.total_queries, label, job.captured_count)
            time.sleep(delay_seconds)
        return job.captured_count
    finally:
        driver.quit()


@transaction.atomic
def store_captured_rows(job, rows):
    for data in rows:
        identity = ProductCode.objects.select_related("product").filter(
            kind=ProductCode.Kind.METRO, code=data["external_id"], supplier__isnull=True
        ).first()
        if identity:
            product, score = identity.product, 100
        else:
            product, score = suggest_product(data["name"], base_unit=data["base_unit"])
        # METRO variants often share only a package size; fuzzy scores around 85
        # are not safe enough to merge catalog rows without human confirmation.
        if score < 100:
            product = None
        previous = MetroScrapedProduct.objects.filter(
            job=job,
            external_id=data["external_id"],
        ).values(
            "price_gross",
            "volume_prices",
            "units_per_package",
            "unit_size",
            "base_unit",
            "imported",
        ).first()
        captured, _ = MetroScrapedProduct.objects.update_or_create(
            job=job,
            external_id=data["external_id"],
            defaults={**data, "matched_product": product, "match_score": score},
        )
        pricing_fields = (
            "price_gross",
            "volume_prices",
            "units_per_package",
            "unit_size",
            "base_unit",
        )
        if previous and previous["imported"] and any(
            previous[field] != getattr(captured, field) for field in pricing_fields
        ):
            captured.imported = False
            captured.save(update_fields=["imported"])
    job.captured_count = job.products.count()
    job.save(update_fields=["captured_count"])
    return job.captured_count


def run_scrape_job(job):
    driver = None
    job.status = MetroScrapeJob.Status.RUNNING
    job.started_at = timezone.now()
    job.error = ""
    job.save(update_fields=["status", "started_at", "error"])
    deadline = time.monotonic() + settings.METRO_SCRAPE_TIMEOUT_MINUTES * 60
    last_saved_url = ""
    consecutive_browser_errors = 0
    try:
        driver = create_metro_driver()
        driver.get(job.start_url)
        dismiss_cookie_banner(driver)
        while time.monotonic() < deadline:
            try:
                job.current_url = driver.current_url[:1000]
                driver.execute_script(OVERLAY_SCRIPT, job.products.count())
                action = driver.execute_script(
                    "const a=document.documentElement.getAttribute('data-pricematch-action');"
                    "document.documentElement.removeAttribute('data-pricematch-action'); return a;"
                )
                if action == "capture":
                    _load_all_visible_cards(driver)
                    raw_rows = driver.execute_script(CARD_DATA_SCRIPT)
                    store_captured_rows(job, normalize_dom_rows(raw_rows))
                    driver.execute_script("window.scrollTo(0, 0)")
                elif action == "watchlist":
                    capture_watchlist(driver, job)
                elif action == "finish":
                    break
                consecutive_browser_errors = 0
                if job.current_url != last_saved_url:
                    job.save(update_fields=["current_url"])
                    last_saved_url = job.current_url
                time.sleep(0.6)
            except NoSuchWindowException:
                break
            except WebDriverException:
                consecutive_browser_errors += 1
                if consecutive_browser_errors >= 5:
                    break
                time.sleep(1)
        job.status = MetroScrapeJob.Status.COMPLETED
    except Exception as exc:
        job.status = MetroScrapeJob.Status.ERROR
        job.error = str(exc)
    finally:
        if driver:
            try:
                driver.quit()
            except Exception as exc:
                logger.debug("Chrome cleanup failed: %s", exc)
        job.captured_count = job.products.count()
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "error", "captured_count", "finished_at", "current_url"])


def launch_scrape_job(job):
    log_dir = Path(settings.MEDIA_ROOT) / "metro_scraper"
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / f"job-{job.pk}.log").open("ab") as log_file:
        # No shell is involved and every argument is controlled by the application.
        subprocess.Popen(  # nosec B603
            [sys.executable, str(Path(settings.BASE_DIR) / "manage.py"), "metro_scrape", str(job.pk)],
            cwd=settings.BASE_DIR,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )


def launch_mass_catalog_job(job, store_query=""):
    log_dir = Path(settings.MEDIA_ROOT) / "metro_scraper"
    log_dir.mkdir(parents=True, exist_ok=True)
    arguments = [
        sys.executable,
        str(Path(settings.BASE_DIR) / "manage.py"),
        "metro_seed_catalog",
        "--resume",
        str(job.pk),
        "--delay",
        "0.8",
        "--retries",
        "3",
    ]
    if store_query:
        arguments.extend(["--store", store_query])
    with (log_dir / f"mass-job-{job.pk}.log").open("ab") as log_file:
        subprocess.Popen(  # nosec B603
            arguments,
            cwd=settings.BASE_DIR,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )


@transaction.atomic
def import_scraped_rows(rows):
    imported = 0
    touched_jobs = set()
    today = timezone.localdate()
    for row in rows.select_related("job", "matched_product").filter(imported=False):
        identity = ProductCode.objects.select_related("product").filter(
            kind=ProductCode.Kind.METRO, code=row.external_id, supplier__isnull=True
        ).first()
        product = identity.product if identity else row.matched_product
        if not product:
            product, _ = Product.objects.get_or_create(
                name=row.name,
                brand="",
                base_unit=row.base_unit,
                defaults={"category": row.category},
            )
        if row.category and product.category in {"", "Altele"}:
            product.category = row.category
            product.save(update_fields=["category"])
        ProductCode.objects.update_or_create(
            kind=ProductCode.Kind.METRO,
            code=row.external_id,
            supplier=None,
            defaults={"product": product},
        )
        source = metro_offer_source(row.store_name or row.job.store_name)
        offer, _ = MetroOffer.objects.update_or_create(
            product=product,
            valid_from=today,
            source=source,
            defaults={
                "units_per_package": row.units_per_package,
                "unit_size": row.unit_size,
                "price_gross": row.price_gross,
                "active": True,
            },
        )
        offer.volume_tiers.all().delete()
        MetroOfferTier.objects.bulk_create(
            [
                MetroOfferTier(
                    offer=offer,
                    min_packages=tier["min_packages"],
                    price_gross=Decimal(tier["price_gross"]),
                    label=tier.get("label", "")[:120],
                )
                for tier in row.volume_prices
            ]
        )
        row.matched_product = product
        row.imported = True
        row.save(update_fields=["matched_product", "imported"])
        update_metro_product_state(row, product)
        touched_jobs.add(row.job_id)
        imported += 1
    for job_id in touched_jobs:
        job = MetroScrapeJob.objects.get(pk=job_id)
        job.imported_count = job.products.filter(imported=True).count()
        job.save(update_fields=["imported_count"])
    return imported

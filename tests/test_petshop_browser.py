"""Real Chrome acceptance for the static local petshop application."""

from pathlib import Path

import pytest

from harness.browser_driver import BrowserDriver
from tests.petshop_helpers import (
    LocalPetshop,
    artifact_directory,
    assert_text,
    browser_plan,
    click,
    criterion,
    dispatch_tab,
    evaluate,
    inspect_controls,
    navigate,
    record_screenshot,
    run,
    scroll_cart_into_view,
)

pytestmark = pytest.mark.browser


@pytest.mark.parametrize("width,height,label", [(1280, 800, "desktop"), (375, 812, "mobile")])
def test_petshop_catalog_cart_and_responsive_layout(tmp_path, width, height, label):
    source = Path(__file__).resolve().parents[1] / "examples" / "petshop"
    shots = artifact_directory(tmp_path)

    with LocalPetshop(source) as site:
        plan = browser_plan(site.url, width, height)
        runtime = tmp_path / f"chrome-runtime-{label}"
        driver = BrowserDriver(plan, runtime)
        try:
            start = driver.run_criterion(
                criterion(
                    navigate(site.url),
                    assert_text(".brand", "Good Paws"),
                    assert_text("#cart-count", "0"),
                    assert_text("#cart-total", "€0.00"),
                    assert_text(".product-card:nth-child(1) .product-name", "Dog Toy"),
                    assert_text(".product-card:nth-child(1) .product-price", "€8.00"),
                    assert_text(".product-card:nth-child(2) .product-name", "Cat Bowl"),
                    assert_text(".product-card:nth-child(2) .product-price", "€12.00"),
                    assert_text(".product-card:nth-child(3) .product-name", "Bird Seed"),
                    assert_text(".product-card:nth-child(3) .product-price", "€5.00"),
                )
            )
            assert start["status"] == "passed", start
            start_shot = record_screenshot(driver, start, f"petshop-{label}-catalog.png", shots)

            result = driver.run_criterion(
                criterion(
                    click('.add-button[aria-label="Add Dog Toy to cart"]'),
                    click('.add-button[aria-label="Add Dog Toy to cart"]'),
                    click('.add-button[aria-label="Add Cat Bowl to cart"]'),
                    assert_text("#cart-count", "3"),
                    assert_text(
                        '[data-product-id="dog-toy"] .cart-item-detail', "Quantity: 2 · €8.00 each"
                    ),
                    assert_text(
                        '[data-product-id="cat-bowl"] .cart-item-detail',
                        "Quantity: 1 · €12.00 each",
                    ),
                    assert_text("#cart-total", "€28.00"),
                )
            )
            assert result["status"] == "passed", result
            scroll_cart_into_view(driver)
            basket_capture = run(
                driver,
                assert_text(
                    '[data-product-id="dog-toy"] .cart-item-detail', "Quantity: 2 · €8.00 each"
                ),
                assert_text(
                    '[data-product-id="cat-bowl"] .cart-item-detail', "Quantity: 1 · €12.00 each"
                ),
                assert_text("#cart-total", "€28.00"),
            )
            basket_shot = record_screenshot(
                driver, basket_capture, f"petshop-{label}-basket-28-eur.png", shots
            )

            result = driver.run_criterion(
                criterion(
                    click('[aria-label="Remove one Dog Toy"]'),
                    assert_text("#cart-count", "2"),
                    assert_text("#cart-total", "€20.00"),
                    assert_text(
                        '[data-product-id="dog-toy"] .cart-item-detail', "Quantity: 1 · €8.00 each"
                    ),
                )
            )
            assert result["status"] == "passed", result
            scroll_cart_into_view(driver)
            removal_capture = run(
                driver,
                assert_text(
                    '[data-product-id="dog-toy"] .cart-item-detail', "Quantity: 1 · €8.00 each"
                ),
                assert_text(
                    '[data-product-id="cat-bowl"] .cart-item-detail', "Quantity: 1 · €12.00 each"
                ),
                assert_text("#cart-total", "€20.00"),
            )
            removal_shot = record_screenshot(
                driver, removal_capture, f"petshop-{label}-basket-20-eur.png", shots
            )

            # Rebuild the two-unit state before testing removal all the way to zero.
            run(driver, click('.add-button[aria-label="Add Dog Toy to cart"]'))
            run(driver, click('.add-button[aria-label="Add Bird Seed to cart"]'))
            run(
                driver,
                click('[aria-label="Remove one Dog Toy"]'),
                assert_text("#cart-count", "3"),
                assert_text("#cart-total", "€25.00"),
            )
            # Repeated removal after the final unit must remove the row without a negative count.
            run(
                driver,
                click('[aria-label="Remove one Dog Toy"]'),
                assert_text("#cart-count", "2"),
                assert_text("#cart-total", "€17.00"),
            )
            zero_row = evaluate(
                driver,
                "({dogRowCount: document.querySelectorAll('[data-product-id=dog-toy]').length, "
                "focusedControl: document.activeElement.getAttribute('aria-label')})",
            )
            assert zero_row == {
                "dogRowCount": 0,
                "focusedControl": "Remove one Cat Bowl",
            }, zero_row

            # Remove remaining items through the visible controls and check the empty state.
            run(
                driver,
                click('[aria-label="Remove one Cat Bowl"]'),
                click('[aria-label="Remove one Bird Seed"]'),
            )
            run(
                driver,
                assert_text("#cart-count", "0"),
                assert_text("#cart-total", "€0.00"),
                assert_text("#empty-cart", "Your basket is waiting for a little something."),
            )
            scroll_cart_into_view(driver)
            empty_capture = run(
                driver,
                assert_text("#cart-count", "0"),
                assert_text("#cart-total", "€0.00"),
                assert_text("#empty-cart", "Your basket is waiting for a little something."),
            )
            empty_shot = record_screenshot(
                driver, empty_capture, f"petshop-{label}-basket-empty.png", shots
            )

            # Reload is a new page-local cart session.
            run(
                driver,
                navigate(site.url),
                assert_text("#cart-count", "0"),
                assert_text("#cart-total", "€0.00"),
            )

            # Keyboard focus and visible focus styling are checked with actual Tab key events.
            evaluate(
                driver,
                "(() => { document.querySelector('.brand').focus(); return {focused: true}; })()",
            )
            dispatch_tab(driver)
            focus = evaluate(
                driver,
                "({label: document.activeElement.getAttribute('aria-label'), "
                "outline: getComputedStyle(document.activeElement).outlineStyle, "
                "outlineWidth: getComputedStyle(document.activeElement).outlineWidth})",
            )
            assert focus["label"] == "Add Dog Toy to cart", focus
            assert focus["outline"] != "none" and focus["outlineWidth"] != "0px", focus
            focus_result = run(
                driver,
                {
                    "op": "assert_visible",
                    "selector": '.add-button[aria-label="Add Dog Toy to cart"]',
                },
            )
            focus_shot = record_screenshot(
                driver, focus_result, f"petshop-{label}-keyboard-focus.png", shots
            )

            metrics = inspect_controls(driver)
            assert metrics["width"] == width and metrics["height"] == height, metrics
            assert metrics["documentWidth"] <= width, metrics
            assert all(
                control["visible"]
                and control["hitTarget"]
                and control["width"] >= 32
                and control["height"] >= 32
                and 0 <= control["left"] < control["right"] <= width
                and 0 <= control["top"] < control["bottom"] <= height
                for control in metrics["controls"]
            ), metrics

            # Clear works from a populated basket and is repeatable from empty.
            run(
                driver,
                click('[aria-label="Add Cat Bowl to cart"]'),
                click('[aria-label="Add Bird Seed to cart"]'),
                click("#clear-cart"),
                assert_text("#cart-count", "0"),
                assert_text("#cart-total", "€0.00"),
                assert_text("#empty-cart", "Your basket is waiting for a little something."),
            )
            run(driver, click("#clear-cart"), assert_text("#cart-count", "0"))

            # Reload a populated basket to establish that quantities do not persist.
            run(
                driver,
                click('[aria-label="Add Cat Bowl to cart"]'),
                click('[aria-label="Add Bird Seed to cart"]'),
                assert_text("#cart-count", "2"),
                assert_text("#cart-total", "€17.00"),
            )
            run(
                driver,
                navigate(site.url),
                assert_text("#cart-count", "0"),
                assert_text("#cart-total", "€0.00"),
            )

            print(
                {
                    "viewport": label,
                    "source": str(source),
                    "screenshots": [start_shot, basket_shot, removal_shot, empty_shot, focus_shot],
                    "layout": metrics,
                }
            )
        finally:
            driver.close()

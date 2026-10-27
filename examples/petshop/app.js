(() => {
  "use strict";

  const products = [
    { id: "dog-toy", name: "Dog Toy", priceCents: 800, icon: "🐕" },
    { id: "cat-bowl", name: "Cat Bowl", priceCents: 1200, icon: "🐈" },
    { id: "bird-seed", name: "Bird Seed", priceCents: 500, icon: "🐦" },
  ];
  const quantities = Object.fromEntries(products.map(({ id }) => [id, 0]));
  const productList = document.querySelector("#product-list");
  const cartItems = document.querySelector("#cart-items");
  const cartCount = document.querySelector("#cart-count");
  const cartTotal = document.querySelector("#cart-total");
  const announcement = document.querySelector("#cart-announcement");
  const clearButton = document.querySelector("#clear-cart");

  const money = (cents) => `€${(cents / 100).toFixed(2)}`;

  function renderCatalog() {
    productList.replaceChildren(...products.map((product) => {
      const card = document.createElement("article");
      card.className = "product-card";

      const art = document.createElement("div");
      art.className = "product-art";
      art.setAttribute("aria-hidden", "true");
      art.textContent = product.icon;

      const name = document.createElement("h2");
      name.className = "product-name";
      name.textContent = product.name;

      const price = document.createElement("p");
      price.className = "product-price";
      price.textContent = money(product.priceCents);

      const add = document.createElement("button");
      add.className = "add-button";
      add.type = "button";
      add.setAttribute("aria-label", `Add ${product.name} to cart`);
      add.textContent = "Add to basket";
      add.addEventListener("click", () => {
        quantities[product.id] += 1;
        renderCart();
        announcement.textContent = `${product.name} added to basket.`;
      });

      card.append(art, name, price, add);
      return card;
    }));
  }

  function renderCart({ focusProductId = null, focusIndex = 0, focusAfterRemoval = false } = {}) {
    const selected = products.filter(({ id }) => quantities[id] > 0);
    const count = products.reduce((sum, { id }) => sum + quantities[id], 0);
    const total = products.reduce((sum, { id, priceCents }) => sum + quantities[id] * priceCents, 0);

    cartItems.replaceChildren(...selected.map((product) => {
      const row = document.createElement("li");
      row.className = "cart-item";
      row.dataset.productId = product.id;

      const info = document.createElement("div");
      const name = document.createElement("p");
      name.className = "cart-item-name";
      name.textContent = product.name;
      const detail = document.createElement("p");
      detail.className = "cart-item-detail";
      detail.textContent = `Quantity: ${quantities[product.id]} · ${money(product.priceCents)} each`;
      const remove = document.createElement("button");
      remove.className = "remove-button";
      remove.type = "button";
      remove.setAttribute("aria-label", `Remove one ${product.name}`);
      remove.textContent = "Remove one";
      remove.addEventListener("click", () => {
        const priorIndex = selected.findIndex(({ id }) => id === product.id);
        quantities[product.id] = Math.max(0, quantities[product.id] - 1);
        const remains = quantities[product.id] > 0;
        renderCart({
          focusProductId: remains ? product.id : null,
          focusIndex: priorIndex,
          focusAfterRemoval: !remains,
        });
        announcement.textContent = remains
          ? `One ${product.name} removed. ${quantities[product.id]} remain.`
          : `${product.name} removed from basket.`;
      });
      info.append(name, detail, remove);

      const linePrice = document.createElement("p");
      linePrice.className = "cart-item-price";
      linePrice.textContent = money(product.priceCents * quantities[product.id]);
      row.append(info, linePrice);
      return row;
    }));

    cartCount.textContent = String(count);
    cartCount.setAttribute("aria-label", `${count} ${count === 1 ? "item" : "items"}`);
    cartTotal.textContent = money(total);

    if (focusProductId) {
      cartItems.querySelector(`[data-product-id="${focusProductId}"] .remove-button`)?.focus();
    } else if (focusAfterRemoval && selected.length > 0) {
      const nearest = selected[Math.min(focusIndex, selected.length - 1)];
      cartItems.querySelector(`[data-product-id="${nearest.id}"] .remove-button`)?.focus();
    } else if (focusAfterRemoval) {
      clearButton.focus();
    }
  }

  clearButton.addEventListener("click", () => {
    for (const product of products) quantities[product.id] = 0;
    renderCart();
    announcement.textContent = "Basket cleared.";
  });

  renderCatalog();
  renderCart();
})();

// AVS SKU tab logic
// This script runs after app.js and can use global `apiFetch` and `regions`.
(function () {
    const PLUGIN_NAME = "avs-sku";
    const container = document.getElementById("plugin-tab-" + PLUGIN_NAME);
    if (!container) return;

    // -----------------------------------------------------------------------
    // 1. Load HTML fragment
    // -----------------------------------------------------------------------
    fetch(`/plugins/${PLUGIN_NAME}/static/html/avs-sku-tab.html`)
        .then(resp => resp.text())
        .then(html => {
            container.innerHTML = html;
            initAvsSkuPlugin();
        })
        .catch(err => {
            container.innerHTML = `<div class="alert alert-danger">Failed to load plugin UI: ${err.message}</div>`;
        });

    // -----------------------------------------------------------------------
    // 2. Plugin initialisation (called after HTML is injected)
    // -----------------------------------------------------------------------
    function initAvsSkuPlugin() {
        const tenantEl = document.getElementById("tenant-select");
        const regionEl = document.getElementById("region-select");
        const byolToggle = document.getElementById("avs-sku-byol-toggle");
        const pricingScopeSelect = document.getElementById("avs-sku-pricing-scope");
        const priceModeSelect = document.getElementById("avs-sku-price-mode");
        const statusEl = document.getElementById("avs-sku-status");
        const cardsEl = document.getElementById("avs-sku-cards");
        const legendEl = document.getElementById("avs-sku-legend");
        let currentItems = [];
        let subscriptionsRefreshPending = false;
        let latestSubscriptions = Array.isArray(window.subscriptions) ? window.subscriptions : [];

        if (
            !tenantEl ||
            !regionEl ||
            !byolToggle ||
            !pricingScopeSelect ||
            !priceModeSelect ||
            !statusEl ||
            !cardsEl ||
            !legendEl
        ) {
            return;
        }

        const priceModeLabel = {
            payg_hour: "PAYG / Hour",
            payg_month: "PAYG / Month",
            reservation_1y_month: "1 Year reservation / Month",
            reservation_3y_month: "3 Years reservation / Month",
            reservation_5y_month: "5 Years reservation / Month",
        };

        function getContext() {
            const tenantId = tenantEl.value || "";
            const region = regionEl.value || "";
            const tenantOpt = tenantEl.selectedOptions?.[0];
            const tenantName = tenantOpt?.text || tenantId || "—";
            const regionObj = (typeof regions !== "undefined" ? regions : []).find(
                r => r.name === region
            );
            const regionName = regionObj?.displayName || region || "—";
            return { tenantId, tenantName, region, regionName };
        }

        function formatNumber(value) {
            if (typeof value !== "number") {
                return "—";
            }
            return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
        }

        function formatPrice(price, mode) {
            if (!price) {
                return "Not available";
            }

            const amount = Number(price[mode] || 0);
            if (!Number.isFinite(amount) || amount <= 0) {
                return "Not available";
            }

            const currency = price.currency_code || "USD";
            const unit = mode === "payg_hour" ? "1 Hour" : "1 Month";
            const roundedAmount = Math.round(amount).toLocaleString();
            return `${roundedAmount} ${currency} / ${unit}`;
        }

        function countActiveSkus(items, mode) {
            if (!Array.isArray(items)) {
                return 0;
            }
            return items.filter(item => Number(item?.price?.[mode] || 0) > 0).length;
        }

        function showLegend() {
            legendEl.classList.remove("avs-sku-hidden");
        }

        function hideLegend() {
            legendEl.classList.add("avs-sku-hidden");
        }

        function getPricingSelection() {
            const value = pricingScopeSelect.value || "public";
            if (value.startsWith("sub:")) {
                return {
                    pricingSource: "subscription",
                    subscriptionId: value.slice(4),
                };
            }
            return {
                pricingSource: "public",
                subscriptionId: "",
            };
        }

        function queueSubscriptionsRefresh(nextSubscriptions) {
            if (subscriptionsRefreshPending) {
                return;
            }
            subscriptionsRefreshPending = true;
            Promise.resolve().then(async () => {
                subscriptionsRefreshPending = false;
                if (Array.isArray(nextSubscriptions)) {
                    latestSubscriptions = nextSubscriptions;
                }
                await refreshPricingScopeOptions();
                if (getContext().region) {
                    loadSkus();
                }
            });
        }

        function eventMatchesCurrentTenant(eventTenantId) {
            const currentTenantId = getContext().tenantId;
            if (!currentTenantId) {
                return true;
            }
            if (!eventTenantId) {
                return true;
            }
            return eventTenantId === currentTenantId;
        }

        function bindCoreContextEvents() {
            document.addEventListener("azscout:subscriptions-loaded", event => {
                if (!eventMatchesCurrentTenant(event?.detail?.tenantId)) {
                    return;
                }
                queueSubscriptionsRefresh(event?.detail?.subscriptions);
            });

            document.addEventListener("azscout:regions-loaded", event => {
                if (!eventMatchesCurrentTenant(event?.detail?.tenantId)) {
                    return;
                }
                if (regionEl.value !== _lastRegion) {
                    _lastRegion = regionEl.value;
                    loadSkus();
                }
            });

            document.addEventListener("azscout:region-changed", event => {
                if (!eventMatchesCurrentTenant(event?.detail?.tenantId)) {
                    return;
                }
                const nextRegion = event?.detail?.region || "";
                if (nextRegion && nextRegion !== regionEl.value) {
                    regionEl.value = nextRegion;
                }
                if (regionEl.value !== _lastRegion) {
                    _lastRegion = regionEl.value;
                    loadSkus();
                }
            });
        }

        async function refreshPricingScopeOptions() {
            const previousValue = pricingScopeSelect.value;
            pricingScopeSelect.innerHTML = '<option value="public">Use public prices list</option>';

            const ctx = getContext();
            if (!ctx.tenantId) {
                pricingScopeSelect.value = "public";
                return;
            }

            const tenantSubscriptions = latestSubscriptions;

            if (tenantSubscriptions.length > 0) {
                const separator = document.createElement("option");
                separator.value = "__subscription-separator__";
                separator.textContent = "Or use subscription based prices";
                separator.disabled = true;
                pricingScopeSelect.appendChild(separator);
            }

            tenantSubscriptions.forEach(subscription => {
                if (!subscription?.id || !subscription?.name) {
                    return;
                }
                const option = document.createElement("option");
                option.value = `sub:${subscription.id}`;
                option.textContent = subscription.name;
                pricingScopeSelect.appendChild(option);
            });

            if (
                previousValue &&
                [...pricingScopeSelect.options].some(option => option.value === previousValue)
            ) {
                pricingScopeSelect.value = previousValue;
            } else {
                pricingScopeSelect.value = "public";
            }
        }

        function renderCards(items, mode, region) {
            if (!Array.isArray(items) || items.length === 0) {
                cardsEl.innerHTML = '<div class="text-body-secondary small">No SKU found for this region.</div>';
                return;
            }

            const orderedItems = [...items].sort((left, right) => {
                const leftActive = Number(left?.price?.[mode] || 0) > 0;
                const rightActive = Number(right?.price?.[mode] || 0) > 0;
                if (leftActive !== rightActive) {
                    return leftActive ? -1 : 1;
                }
                return String(left?.name || "").localeCompare(String(right?.name || ""));
            });

            cardsEl.innerHTML = orderedItems
                .map(item => {
                    const t = item.technical || {};
                    const price = item.price || {};
                    const hasSelectedModePrice = Number(price[mode] || 0) > 0;
                    const priceClass = hasSelectedModePrice ? "text-success" : "text-body-secondary";
                    const unavailableClass = hasSelectedModePrice ? "" : " avs-sku-card-unavailable";
                    const generationLabels = Array.isArray(item.generation_labels)
                        ? item.generation_labels
                        : [];
                    const generationMarkup = generationLabels
                        .map(label => {
                            const generationClass = label === "Generation 2"
                                ? "avs-sku-generation-badge-gen2"
                                : "avs-sku-generation-badge-gen1";
                            return `<span class="avs-sku-generation-badge ${generationClass}">${label}</span>`;
                        })
                        .join("");
                    const generationRowMarkup = generationMarkup
                        ? `<div class="avs-sku-generation-row">${generationMarkup}</div>`
                        : "";
                    return `
                    <article class="avs-sku-card-item${unavailableClass}">
                        <div class="avs-sku-card-header">
                            <div class="avs-sku-title-wrap">
                                <h6 class="mb-0">${item.name}</h6>
                                ${generationRowMarkup}
                            </div>
                            <span class="avs-sku-price ${priceClass}">${formatPrice(price, mode)}</span>
                        </div>
                        <div class="avs-sku-specs">
                            <div><span>Cores</span><strong>${formatNumber(t.cores)}</strong></div>
                            <div><span>RAM (GB)</span><strong>${formatNumber(t.ram)}</strong></div>
                            <div><span>CPU</span><strong>${t.cpu_model || "—"}</strong></div>
                            <div><span>Arch</span><strong>${t.cpu_architecture || "—"}</strong></div>
                            <div><span>vSAN</span><strong>${t.vsan_architecture || "—"}</strong></div>
                            <div><span>Capacity (TB)</span><strong>${formatNumber(t.vsan_capacity_tier_in_tb)}</strong></div>
                        </div>
                    </article>
                `;
                })
                .join("");
        }

        function rerenderCurrentItems() {
            const ctx = getContext();
            renderCards(currentItems, priceModeSelect.value, ctx.region);
        }

        async function loadSkus() {
            const ctx = getContext();
            const byol = byolToggle.checked;
            const { pricingSource, subscriptionId } = getPricingSelection();
            const selectedModeLabel = priceModeLabel[priceModeSelect.value] || priceModeSelect.value;

            if (!ctx.region) {
                statusEl.textContent = "Select a region to display AVS SKUs.";
                currentItems = [];
                cardsEl.innerHTML = "";
                hideLegend();
                return;
            }

            byolToggle.disabled = true;
            pricingScopeSelect.disabled = true;
            hideLegend();
            statusEl.textContent = "";
            cardsEl.innerHTML =
                `<div class="alert alert-info fade show" role="alert" style="grid-column: 1 / -1;">` +
                `<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>` +
                `Loading AVS SKUs for ${ctx.regionName} (${pricingSource}, ${byol ? "BYOL" : "No BYOL"}, ${selectedModeLabel})…` +
                `</div>`;

            try {
                const qs = new URLSearchParams({
                    region: ctx.region,
                    byol: String(byol),
                    pricing_source: pricingSource,
                });
                if (pricingSource === "subscription" && subscriptionId) {
                    qs.set("subscription_id", subscriptionId);
                }
                const data = await apiFetch(`/plugins/${PLUGIN_NAME}/skus?${qs.toString()}`);
                currentItems = data.items || [];
                rerenderCurrentItems();
                const activeCount = countActiveSkus(currentItems, priceModeSelect.value);
                statusEl.textContent =
                    `${activeCount} SKU ${activeCount === 1 ? "is" : "are"} available in ${ctx.regionName} (${pricingSource}, ${selectedModeLabel}).`;
                showLegend();
            } catch (e) {
                currentItems = [];
                cardsEl.innerHTML =
                    `<div class="alert alert-danger alert-dismissible fade show" role="alert" style="grid-column: 1 / -1;">` +
                    `<strong>Error:</strong> ${e.message}` +
                    `<button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>` +
                    `</div>`;
                statusEl.textContent = "";
                hideLegend();
            } finally {
                byolToggle.disabled = false;
                pricingScopeSelect.disabled = false;
            }
        }

        let _lastRegion = regionEl?.value || "";

        byolToggle.addEventListener("change", loadSkus);
        pricingScopeSelect.addEventListener("change", () => {
            loadSkus();
        });
        priceModeSelect.addEventListener("change", () => {
            if (!Array.isArray(currentItems) || currentItems.length === 0) {
                return;
            }
            const ctx = getContext();
            const { pricingSource } = getPricingSelection();
            const selectedModeLabel = priceModeLabel[priceModeSelect.value] || priceModeSelect.value;
            rerenderCurrentItems();
            const activeCount = countActiveSkus(currentItems, priceModeSelect.value);
            statusEl.textContent =
                `Showing ${activeCount} SKU ${activeCount === 1 ? "card" : "cards"} for ${ctx.regionName} (${pricingSource}, ${selectedModeLabel}).`;
        });

        hideLegend();
        bindCoreContextEvents();
        refreshPricingScopeOptions().then(() => {
            loadSkus();
        });
    }
})();

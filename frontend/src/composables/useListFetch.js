import { ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api } from "../api/client";

const PAGE_SIZES = [20, 50, 100];

export function useListFetch(endpoint, options = {}) {
  const route = useRoute();
  const router = useRouter();
  const cacheKey = options.cacheKey || endpoint;
  const clientFilter = options.clientFilter;
  const status = ref("loading");
  const message = ref("");
  const stale = ref("");
  const items = ref([]);
  const rawItems = ref([]);
  const total = ref(0);

  const page = ref(Number(route.query.page) || 1);
  const pageSize = ref(PAGE_SIZES.includes(Number(route.query.page_size)) ? Number(route.query.page_size) : 20);
  const search = ref(route.query.q || "");

  function applyClientFilter(list) {
    if (!clientFilter || !search.value.trim()) return list;
    const q = search.value.trim().toLowerCase();
    return list.filter((row) => clientFilter(row, q));
  }

  function syncQuery() {
    const query = { ...route.query, page: String(page.value), page_size: String(pageSize.value) };
    if (search.value) query.q = search.value;
    else delete query.q;
    router.replace({ query });
  }

  async function load(attempt = 0) {
    status.value = "loading";
    stale.value = "";
    try {
      const response = await api.get(endpoint, { params: { page: page.value, page_size: pageSize.value } });
      const payload = response.data.data;
      let list = Array.isArray(payload) ? payload : payload.items || [];
      if (!Array.isArray(payload) && !payload.items && payload && typeof payload === "object") {
        list = [payload];
      }
      rawItems.value = list.filter((item) => item && typeof item === "object");
      items.value = applyClientFilter(rawItems.value);
      total.value = payload.total ?? rawItems.value.length;
      status.value = items.value.length ? "ready" : "empty";
      localStorage.setItem(`cache:${cacheKey}`, JSON.stringify({ at: new Date().toISOString(), items: rawItems.value }));
    } catch (err) {
      if (err.code === "ECONNABORTED" && attempt < 1) {
        await load(attempt + 1);
        return;
      }
      if (err.code === "ECONNABORTED") {
        status.value = "timeout";
        message.value = "请求超时，已重试 1 次仍失败";
        return;
      }
      if (err.response?.status === 403) {
        status.value = "forbidden";
        return;
      }
      const cached = localStorage.getItem(`cache:${cacheKey}`);
      if (cached) {
        const parsed = JSON.parse(cached);
        rawItems.value = parsed.items;
        items.value = applyClientFilter(rawItems.value);
        stale.value = parsed.at;
        status.value = items.value.length ? "ready" : "empty";
        return;
      }
      status.value = "error";
      message.value = err.response?.data?.message || "加载失败";
    }
  }

  let searchTimer = 0;
  function onSearch(value) {
    search.value = value;
    clearTimeout(searchTimer);
    searchTimer = window.setTimeout(() => {
      page.value = 1;
      syncQuery();
      if (clientFilter) {
        items.value = applyClientFilter(rawItems.value);
        status.value = items.value.length ? "ready" : "empty";
      } else {
        load(0);
      }
    }, 300);
  }

  function setPage(p) {
    page.value = p;
    syncQuery();
    load(0);
  }

  function setPageSize(size) {
    pageSize.value = size;
    page.value = 1;
    syncQuery();
    load(0);
  }

  watch(
    () => route.fullPath,
    () => {
      page.value = Number(route.query.page) || 1;
      pageSize.value = PAGE_SIZES.includes(Number(route.query.page_size)) ? Number(route.query.page_size) : 20;
      search.value = route.query.q || "";
      load(0);
    },
    { immediate: true },
  );

  return {
    PAGE_SIZES,
    status,
    message,
    stale,
    items,
    total,
    page,
    pageSize,
    search,
    load,
    onSearch,
    setPage,
    setPageSize,
  };
}
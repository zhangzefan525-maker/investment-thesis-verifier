// 接口地址一律走这里，不写死 `/api/...`。
//
// 起因：上线时这个产品挂在现有站点的子路径 `/thesis/` 下（同一台机器上还跑着
// 另一个项目，不能占根路径）。写死的 `/api/verify` 会打到站点根上，
// 请求落到另一个项目的路由里去——而且不会报错，只会 404 或返回别人的页面。
//
// BASE_URL 由 vite 的 `base` 决定：本地开发是 `/`，线上构建是 `/thesis/`。
// 所以这里既保证线上带前缀，又不影响本地开发（proxy 仍然按 `/api` 转发）。
const BASE = import.meta.env.BASE_URL.replace(/\/$/, '')

/** 把 '/api/verify' 拼成 '/thesis/api/verify'（本地则是 '/api/verify'）。 */
export function api(path) {
  return `${BASE}${path}`
}

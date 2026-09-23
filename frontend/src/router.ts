import { createRouter, createWebHistory } from "vue-router";

import { boot } from "./lib/boot";

// The document is served at /folt AND at /folt/<anything> -- hooks.website_route_rules maps every
// deep path onto the same page and www/folt.py hands the tail over as `app_path`. The base must
// match or every link the router builds points somewhere frappe does not serve. No trailing slashes
// and no .html in any route: frappe's nginx 301-redirects both.
const BASE = "/folt/";

export const router = createRouter({
  history: createWebHistory(BASE),
  routes: [
    { path: "/", name: "tasks", component: () => import("./views/TasksView.vue") },
    { path: "/workflows", name: "workflows", component: () => import("./views/WorkflowsView.vue") },
    { path: "/w/:slug", name: "list", component: () => import("./views/ListView.vue"), props: true },
    {
      // `(.+)` rather than the default segment matcher: Salary Slip names contain slashes.
      path: "/d/:slug/:name(.+)",
      name: "document",
      component: () => import("./views/DocumentView.vue"),
      props: true,
    },
    { path: "/new", name: "new", component: () => import("./views/NewView.vue") },
    { path: "/new/:slug", name: "create", component: () => import("./views/CreateView.vue"), props: true },
    { path: "/notifications", name: "notifications", component: () => import("./views/NotificationsView.vue") },
    // Anything else is somebody's stale bookmark; send them to their tasks rather than a blank.
    { path: "/:rest(.*)", redirect: { name: "tasks" } },
  ],
  scrollBehavior: (_to, _from, saved) => saved ?? { top: 0 },
});

/** The server already knows which deep link was asked for, so the first paint is the right screen
 *  rather than My Tasks flashing before the router catches up. */
export function initialRoute(): string {
  return boot.app_path ? `/${boot.app_path}` : "/";
}

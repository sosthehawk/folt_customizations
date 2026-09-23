import { createApp } from "vue";

import App from "./App.vue";
import { initialRoute, router } from "./router";
import "./styles/tokens.css";

// Seeded from the boot payload so a deep link paints its own screen first. See www/folt.py.
router.replace(initialRoute()).catch(() => router.replace("/"));

createApp(App).use(router).mount("#folt-app");

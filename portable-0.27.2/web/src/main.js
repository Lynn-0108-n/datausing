import { createApp } from "vue";
import ElementPlus from "element-plus";
import "element-plus/dist/index.css";
import "./style.css";
import { initTheme } from "./theme";
import App from "./App.vue";

// 先定主题再挂载，避免首屏闪一下白底
initTheme();

createApp(App).use(ElementPlus).mount("#app");

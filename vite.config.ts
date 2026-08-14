import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import vueJsx from "@vitejs/plugin-vue-jsx";
import AutoImport from "unplugin-auto-import/vite";
import Components from "unplugin-vue-components/vite";
import { VueRouterAutoImports } from "unplugin-vue-router";
import VueRouter from "unplugin-vue-router/vite";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [
    VueRouter({
      routesFolder: "src/views",
    }),
    vue(),
    vueJsx(),
    tailwindcss(),
    AutoImport({
      imports: [
        "vue",
        VueRouterAutoImports,
        {
          pinia: ["defineStore", "storeToRefs"],
          firebase: ["signInWithPopup", "GoogleAuthProvider", "onAuthStateChanged", "signOut"],
          "firebase/auth": ["signInWithPopup", "GoogleAuthProvider", "onAuthStateChanged", "signOut"],
        },
      ],
      dts: "src/auto-imports.d.ts",
      eslintrc: {
        enabled: true,
      },
    }),
    Components({
      dirs: ["src/components"],
      extensions: ["vue"],
      dts: "src/components.d.ts",
    }),
  ],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
});

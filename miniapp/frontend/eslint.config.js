// Минимальный линт: только необъявленные идентификаторы (ReferenceError в рантайме —
// класс бага «forSitter is not defined», из-за которого экран становился пустым).
//  - no-undef: переменные и функции
//  - react/jsx-no-undef: компоненты в JSX (<Foo />), которые no-undef не видит
import globals from "globals";
import react from "eslint-plugin-react";

export default [
  // _design_refs — макеты-референсы дизайна: не импортируются приложением и не попадают в бандл.
  { ignores: ["dist/**", "node_modules/**", "src/_design_refs/**"] },
  // В коде есть `eslint-disable … react-hooks/exhaustive-deps`: заглушка плагина (без правил),
  // чтобы такие директивы не падали «Definition for rule not found». Хуки мы не линтим.
  {
    files: ["src/**/*.{js,jsx}"],
    plugins: { "react-hooks": { rules: { "exhaustive-deps": { meta: { schema: [] }, create: () => ({}) } } } },
    linterOptions: { reportUnusedDisableDirectives: "off" },
  },
  {
    files: ["src/**/*.{js,jsx}"],
    plugins: { react },
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      parserOptions: { ecmaFeatures: { jsx: true } },
      globals: { ...globals.browser },
    },
    rules: {
      "no-undef": "error",
      "react/jsx-no-undef": "error",
    },
  },
];

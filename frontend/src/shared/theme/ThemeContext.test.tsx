// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { ThemeProvider, ThemeToggle, useTheme } from "./ThemeContext";

function ThemeProbe() {
  const { theme, setTheme } = useTheme();
  return (
    <div>
      <span data-testid="theme-value">{theme}</span>
      <button type="button" onClick={() => setTheme("light")}>
        Set light
      </button>
      <ThemeToggle />
    </div>
  );
}

describe("ThemeContext", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.className = "";
    delete document.documentElement.dataset.dsTheme;
    document.documentElement.style.colorScheme = "";
  });

  afterEach(cleanup);

  it("defaults to dark theme and syncs document root attributes", () => {
    render(
      <ThemeProvider>
        <ThemeProbe />
      </ThemeProvider>,
    );

    expect(screen.getByTestId("theme-value").textContent).toBe("dark");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(document.documentElement.dataset.dsTheme).toBe("dark");
    expect(document.documentElement.style.colorScheme).toBe("dark");
  });

  it("restores light theme from localStorage under ds.theme", () => {
    localStorage.setItem("ds.theme", "light");
    render(
      <ThemeProvider>
        <ThemeProbe />
      </ThemeProvider>,
    );

    expect(screen.getByTestId("theme-value").textContent).toBe("light");
    expect(document.documentElement.classList.contains("dark")).toBe(false);
    expect(document.documentElement.dataset.dsTheme).toBe("light");
    expect(document.documentElement.style.colorScheme).toBe("light");
  });

  it("toggles between dark and light and persists choice", () => {
    render(
      <ThemeProvider>
        <ThemeProbe />
      </ThemeProvider>,
    );

    const toggle = screen.getByRole("button", { name: "Switch to light theme" });
    fireEvent.click(toggle);

    expect(screen.getByTestId("theme-value").textContent).toBe("light");
    expect(localStorage.getItem("ds.theme")).toBe("light");
    expect(document.documentElement.classList.contains("dark")).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "Switch to dark theme" }));

    expect(screen.getByTestId("theme-value").textContent).toBe("dark");
    expect(localStorage.getItem("ds.theme")).toBe("dark");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
  });
});

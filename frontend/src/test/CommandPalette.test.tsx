import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { CommandPalette } from "../components/CommandPalette";

describe("CommandPalette", () => {
  it("filters commands and runs the highlighted one with Enter", async () => {
    const runA = vi.fn();
    const runB = vi.fn();
    const onClose = vi.fn();
    render(
      <CommandPalette
        open
        onClose={onClose}
        commands={[
          { id: "a", group: "Actions", label: "Add data", run: runA },
          { id: "b", group: "Tables", label: "ລາຍງານການໂທ Branches", run: runB },
        ]}
      />,
    );
    const input = await screen.findByRole("textbox", { name: "Command" });
    await userEvent.type(input, "branches");
    expect(screen.queryByText("Add data")).not.toBeInTheDocument();
    await userEvent.keyboard("{Enter}");
    expect(runB).toHaveBeenCalledOnce();
    expect(runA).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });

  it("closes on Escape", async () => {
    const onClose = vi.fn();
    render(<CommandPalette open onClose={onClose} commands={[]} />);
    await screen.findByRole("textbox", { name: "Command" });
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalled();
  });
});

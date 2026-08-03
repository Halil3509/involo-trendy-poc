import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { OnboardingForm } from "@/components/onboarding-form";
import { api } from "@/lib/api";

const replace = vi.fn();
const refresh = vi.fn();
const refreshUser = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, refresh }),
}));
vi.mock("@/components/auth-provider", () => ({
  useAuth: () => ({ refreshUser }),
}));
vi.mock("@/lib/api", () => ({
  api: {
    getPreferences: vi.fn(),
    updatePreferences: vi.fn(),
  },
}));

describe("OnboardingForm", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getPreferences).mockResolvedValue({
      target_countries: [],
      target_cities: [],
      content_languages: ["en-US"],
      timezone: "Europe/Istanbul",
      niches: [],
      goals: [],
      constraints: [],
      updated_at: null,
    });
    vi.mocked(api.updatePreferences).mockImplementation(async (preferences) => ({
      ...preferences,
      updated_at: "2026-07-17T18:00:00Z",
    }));
  });

  it("validates required creator context with accessible field errors", async () => {
    const user = userEvent.setup();
    render(<OnboardingForm />);
    await screen.findByRole("heading", { name: "Make every brief feel like yours" });

    await user.click(screen.getByRole("button", { name: "Save and continue" }));

    expect(screen.getByLabelText("Target market")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByText("Choose a target market.")).toBeInTheDocument();
    expect(screen.getByText("Choose at least one goal.")).toBeInTheDocument();
    expect(api.updatePreferences).not.toHaveBeenCalled();
  });

  it("saves market, language, timezone, niche, goals, and constraints", async () => {
    const user = userEvent.setup();
    render(<OnboardingForm />);
    await screen.findByRole("heading", { name: "Make every brief feel like yours" });

    await user.selectOptions(screen.getByLabelText("Target market"), "TR");
    await user.type(screen.getByLabelText("Creator niche"), "Food education");
    await user.click(screen.getByLabelText("Increase saves"));
    await user.type(screen.getByLabelText("Production constraints"), "One-person crew");
    await user.click(screen.getByRole("button", { name: "Save and continue" }));

    expect(api.updatePreferences).toHaveBeenCalledWith(
      expect.objectContaining({
        target_countries: ["TR"],
        target_cities: [],
        content_languages: ["en-US"],
        timezone: "Europe/Istanbul",
        niches: ["Food education"],
        goals: ["Increase saves"],
        constraints: ["One-person crew"],
      }),
    );
    expect(refreshUser).toHaveBeenCalled();
    expect(replace).toHaveBeenCalledWith("/dashboard");
  });
});

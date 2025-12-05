if (Sentry) {
	Sentry.init({ dsn: window.local.sentryDsn });
}

function getStoredTheme() {
	const name = "theme=";
	const decodedCookie = decodeURIComponent(document.cookie);
	const ca = decodedCookie.split(";");
	for (let i = 0; i < ca.length; i++) {
		let c = ca[i];
		while (c.charAt(0) === " ") {
			c = c.substring(1);
		}
		if (c.indexOf(name) === 0) {
			return c.substring(name.length, c.length);
		}
	}
	return null;
}
const setStoredTheme = (theme) => {
	const domain = `${document.domain.match(/[^\.]*\.[^.]*$/)[0]};`;
	document.cookie = `theme=${theme};path=/;domain=.${domain}`;
};

const getPreferredTheme = () => {
	const storedTheme = getStoredTheme();
	if (storedTheme && ["light", "dark", "auto"].includes(storedTheme)) {
		return storedTheme;
	}
	return "auto";
};

const getAbsTheme = (theme) => {
	if (!window.matchMedia) {
		return "light";
	}
	if (theme === "auto") {
		return window.matchMedia("(prefers-color-scheme: dark)").matches
			? "dark"
			: "light";
	}
	return theme;
};

const getCurrentTheme = () => {
	const theme = getPreferredTheme();
	return getAbsTheme(theme);
};

const setTheme = (theme) => {
	const detectedTheme = getAbsTheme(theme);
	for (const el of document.querySelectorAll("[data-bs-theme]")) {
		el.setAttribute("data-bs-theme", detectedTheme);
	}
};

(() => {
	setTheme(getPreferredTheme());

	if (window.matchMedia) {
		window
			.matchMedia("(prefers-color-scheme: dark)")
			.addEventListener("change", () => {
				setTheme(getPreferredTheme());
			});
	}

	const showActiveTheme = (theme) => {
		const svgOfActiveBtn = document.querySelector(".theme-selector-icon use");
		const tooltips = {
			auto: "Auto brightness",
			dark: "Dark mode",
			light: "Bright Mode",
		};
		const icons = {
			auto: "auto",
			dark: "moon",
			light: "sun",
		};
		document
			.querySelector(".theme-selector")
			?.setAttribute("title", tooltips[theme]);
		document
			.querySelector(".theme-selector")
			?.setAttribute("data-bs-original-title", tooltips[theme]);
		document
			.querySelector(".theme-selector")
			?.setAttribute("aria-label", tooltips[theme]);
		svgOfActiveBtn?.setAttribute("xlink:href", `#icon-${icons[theme]}`);
	};

	window.addEventListener("DOMContentLoaded", () => {
		showActiveTheme(getPreferredTheme());
		for (const toggle of document.querySelectorAll(".theme-selector")) {
			new bootstrap.Tooltip(toggle, { customClass: "navbarTooltip" });
			toggle.addEventListener("click", () => {
				bootstrap.Tooltip.getInstance(".theme-selector").hide();
				let theme = getPreferredTheme();
				if (theme === "auto") {
					theme = "dark";
				} else if (theme === "dark") {
					theme = "light";
				} else {
					theme = "auto";
				}
				setStoredTheme(theme);
				setTheme(theme);
				showActiveTheme(theme);
			});
		}
	});
})();

if (needFlagsEmojiPolyfill) {
	document.body.classList.add("flags-polyfill");
}

async function checkVersion() {
	try {
		const resp = await fetch(`${window.local.apiRoot}version`)
			.then((r) => r.json())
			.catch(() => {});
		if (resp && resp.v !== window.local.siteVersion) {
			window.local.siteVersion = resp.v;
			console.log(`New Version Available! ${resp.v}`);
		}
	} catch {}
}
setInterval(checkVersion, 20e3);
checkVersion();

const tooltipTriggerList = document.querySelectorAll(
	'[data-bs-toggle="tooltip"]',
);
const tooltipList = [...tooltipTriggerList].map(
	(tooltipTriggerEl) => new bootstrap.Tooltip(tooltipTriggerEl),
);

console.log(`
____________________________
|                _____     |
|              / ____  \\   |
|             / /  _ \\  \\  |
|    _____   | |  //  | |  |
|  / ____  \\  \\ \\//_ / /   |
| / /  _ \\  \\  \\  ___ /    |
|| |  //  | |  //  _____   |
| \\ \\//_ / /  // / ____  \\ |
|  \\  ___ /  // / /  _ \\  \\|
|  //       // | |  //  | ||
| //       //   \\ \\//_ / / |
|//       //     \\  ___ /  |
|/       //      //        |
|       //      //         |
|__________________________|

ROUTECHOICES.COM
Version: ${window.local.siteVersion}`);

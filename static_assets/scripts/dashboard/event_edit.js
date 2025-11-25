const seletizeOptions = {
	valueField: "id",
	labelField: "device_id",
	searchField: "device_id",
	create: false,
	createOnBlur: false,
	persist: false,
	plugins: ["preserve_on_blur", "change_listener"],
	load: (query, callback) => {
		if (query.length < 4) {
			return callback();
		}
		reqwest({
			url: `${window.local.apiBaseUrl}search/device?q=${encodeURIComponent(query)}`,
			method: "get",
			type: "json",
			withCredentials: true,
			crossOrigin: true,
			success: (res) => {
				callback(res.results);
			},
			error: () => {
				callback();
			},
		});
	},
};

const createTagWidget = (i) => {
	new TomSelect(i, {
		persist: false,
		createOnBlur: true,
		create: true,
		delimiter: " ",
	});
};

const createColorWidget = (i) => {
	const originalInput = u(i);
	originalInput.hide();
	let color = originalInput.val();
	const colorModal = bootstrap.Modal.getInstance(
		document.getElementById("color-modal"),
	);
	const colorSelector = u("<b>")
		.addClass("me-2")
		.css({ color, cursor: "pointer" })
		.html("&#11044;")
		.on("click", (e) => {
			e.preventDefault();

			u("#color-picker").html("");
			new iro.ColorPicker("#color-picker", {
				color,
				width: 150,
				display: "inline-block",
			}).on("color:change", (c) => {
				color = c.hexString;
			});

			function saveColor() {
				colorModal.hide();
				u("#save-color").off("click");
				u("#color-modal").off("keypress");

				originalInput.val(color);
				colorSelector.css({ color });
			}

			u("#save-color").on("click", saveColor);

			u("#color-modal").on("keypress", (e) => {
				e.preventDefault();
				if (e.which === 13) {
					saveColor();
				}
			});

			colorModal.show();
		});
	const clearColor = u("<button>")
		.addClass("btn btn-info btn-sm")
		.attr("type", "button")
		.html("Reset")
		.on("click", (e) => {
			e.preventDefault();
			selectColorWidget.remove();
			originalInput.after(setBtn);
			originalInput.val("");
		});
	const selectColorWidget = u("<div>")
		.addClass("text-nowrap")
		.append(colorSelector)
		.append(clearColor);
	const setBtn = u("<button>")
		.addClass("btn btn-info btn-sm")
		.attr("type", "button")
		.html('<i class="fa-solid fa-palette"></i>')
		.on("click", (e) => {
			e.preventDefault();
			color = `#${(((1 << 24) * Math.random()) | 0).toString(16).padStart(6, "0")}`;
			colorSelector.css({ color });
			setBtn.remove();
			originalInput.after(selectColorWidget);
		});
	originalInput.on("set", (e) => {
		color = e.detail[0].color;
		if (originalInput.val() === "") {
			colorSelector.css({ color });
			setBtn.remove();
			originalInput.after(selectColorWidget);
		} else {
			colorSelector.css({ color });
		}
		originalInput.val(color);
	});
	if (i.value === "") {
		originalInput.after(setBtn);
	} else {
		originalInput.after(selectColorWidget);
	}
};

const createStartTimeWidget = (i) => {
	makeTimeFieldClearable(i);
	makeFieldNowable(i);
	new tempusDominus.TempusDominus(i);
	i.addEventListener(tempusDominus.Namespace.events.change, (e) => {
		showLocalTime(e.target);
	});
	showLocalTime(i);
};

function onAddedCompetitorRow(row) {
	new TomSelect(
		u(row).find('select[name$="-device"]').first(),
		seletizeOptions,
	);

	createColorWidget(u(row).find(".color-input").first());
	createTagWidget(u(row).find(".tag-input").first());
	createStartTimeWidget(u(row).find('input[id$="-start_time"]').first());
}

function clearEmptyCompetitorRows() {
	u(".formset_row").each((e) => {
		const row = u(e);
		if (
			row
				.find("input")
				.filter((input) => input.type !== "hidden" && input.value !== "")
				.length === 0
		) {
			row.find(".delete-row").first().click();
		}
	});
}

function addCompetitor(name, shortName, startTime, deviceId, color, tags) {
	u(".add-competitor-btn").first().click();
	const lastFormsetRow = u(u(".formset_row").last());
	const inputs = lastFormsetRow.find("input").nodes;
	if (startTime) {
		inputs[5].value = dayjs(startTime).local().format("YYYY-MM-DD HH:mm:ss");
		u(inputs[5]).trigger(tempusDominus.Namespace.events.change);
	}
	inputs[2].value = name;
	inputs[3].value = shortName;
	if (color && /^#([0-9a-fA-F]{3}){1,2}$/.test(color)) {
		u(inputs[6]).trigger("set", { color });
	}
	if (tags) {
		const control = lastFormsetRow
			.find(".tag-input.tomselected")
			.first().tomselect;
		for (const t of tags) {
			control.addOption({ value: t, text: t });
			control.addItem(t);
		}
	}
	if (deviceId) {
		const myDeviceSelectInput = lastFormsetRow
			.find('select[name$="-device"]')
			.first().tomselect;
		reqwest({
			url: `${window.local.apiBaseUrl}search/device?q=${deviceId}`,
			method: "get",
			type: "json",
			withCredentials: true,
			crossOrigin: true,
			success: ((line) => (res) => {
				if (res.results.length === 1) {
					const r = res.results[0];
					myDeviceSelectInput.addOption(r);
					myDeviceSelectInput.setValue(r[seletizeOptions.valueField]);
				}
			})(),
		});
	}
}

function onIofXMLLoaded(e) {
	const file = e.target.files[0];
	if (file) {
		const reader = new FileReader();
		reader.onload = (evt) => {
			const txt = evt.target.result;
			const parser = new DOMParser();
			const parsedXML = parser.parseFromString(txt, "text/xml");
			const isResultFile =
				parsedXML.getElementsByTagName("ResultList").length === 1;
			const isStartFile =
				parsedXML.getElementsByTagName("StartList").length === 1;
			if (!isResultFile && !isStartFile) {
				swal({
					title: "Error!",
					text: "Neither a start list or a result list",
					type: "error",
					confirmButtonText: "OK",
				});
				u("#iof_input").val("");
				return;
			}
			const classes = [];
			const selector = document.getElementById("iof_class_input");
			selector.innerHTML = "";
			let ii = 1;
			for (c of parsedXML.getElementsByTagName("Class")) {
				const id = ii;
				const name = c.getElementsByTagName("Name")[0].textContent;
				classes.push({ id, name });
				const opt = document.createElement("option");
				opt.value = id;
				opt.appendChild(document.createTextNode(name));
				selector.appendChild(opt);
				ii++;
			}
			u("#iof-step-1").addClass("d-none");
			u("#iof-step-2").removeClass("d-none");
			u("#iof-class-cancel-btn").on("click", (e) => {
				e.preventDefault();
				u("#iof-step-2").addClass("d-none");
				u("#iof-step-1").removeClass("d-none");
				u("#iof_input").val("");
			});
			u("#iof-class-submit-btn").off("click");
			u("#iof-class-submit-btn").on("click", (e) => {
				e.preventDefault();
				const classId = u("#iof_class_input").val();
				const suffix = isResultFile ? "Result" : "Start";

				clearEmptyCompetitorRows();
				let ii = 1;
				for (c of parsedXML.getElementsByTagName(`Class${suffix}`)) {
					if (ii === Number.parseInt(classId, 10)) {
						for (p of c.getElementsByTagName(`Person${suffix}`)) {
							let startTime = null;
							let name = null;
							let shortName = null;
							try {
								startTime = p
									.getElementsByTagName(suffix)[0]
									.getElementsByTagName("StartTime")[0].textContent;
							} catch (e) {
								console.log(e);
							}
							try {
								name = `${
									p
										.getElementsByTagName("Person")[0]
										.getElementsByTagName("Given")[0].textContent
								} ${
									p
										.getElementsByTagName("Person")[0]
										.getElementsByTagName("Family")[0].textContent
								}`;
								shortName = `${
									p
										.getElementsByTagName("Person")[0]
										.getElementsByTagName("Given")[0].textContent[0]
								}.${
									p
										.getElementsByTagName("Person")[0]
										.getElementsByTagName("Family")[0].textContent
								}`;
							} catch (e) {
								console.log(e);
							}
							if (name) {
								addCompetitor(name, shortName, startTime);
							}
						}
						u(".add-competitor-btn").first().click();
					}
					ii++;
				}
				u("#iof-step-2").addClass("d-none");
				u("#iof-step-1").removeClass("d-none");
				u("#iof_input").val("");
			});
		};
		reader.onerror = () => {
			swal({
				title: "Error!",
				text: "Could not parse this file",
				type: "error",
				confirmButtonText: "OK",
			});
		};
		reader.readAsText(file, "UTF-8");
	}
}

function onCsvParsed(result) {
	u("#csv_input").val("");
	let errors = "";
	if (result.errors.length > 0) {
		errors = "No line found";
	}
	if (!errors) {
		for (const l of result.data) {
			let empty = false;
			if (l.length === 1 && l[0] === "") {
				empty = true;
			}
			if (!empty && ![4, 5, 6].includes(l.length)) {
				errors = "Each row should have between 4 and 6 columns";
			} else {
				if (!empty && l[2]) {
					try {
						new Date(l[2]);
					} catch (e) {
						errors = "One row contains an invalid date";
					}
				}
			}
		}
	}
	if (errors) {
		swal({
			title: "Error!",
			text: `Could not parse this file: ${errors}`,
			type: "error",
			confirmButtonText: "OK",
		});
		return;
	}
	clearEmptyCompetitorRows();
	for (const l of result.data) {
		if (l.length !== 1) {
			addCompetitor(l[0], l[1], l[2], l?.[3], l?.[4], l?.[5]?.split(" "));
		}
	}
	u(".add-competitor-btn").first().click();
}

function showLocalTime(el) {
	const val = u(el).val();
	if (val) {
		let local = dayjs(val).local(true).utc().format("YYYY-MM-DD HH:mm:ss");
		local += local === "Invalid Date" ? "" : " UTC";
		u(el).closest(":has(.local_time)").find(".local_time").text(local);
	} else {
		u(el)
			.closest(":has(.local_time)")
			.find(".local_time")
			.html("&ZeroWidthSpace;");
	}
}

(() => {
	// set timezone to local
	const userTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
	console.log(`User timezone: ${userTimezone}`);
	const timezoneInput = document.getElementById("id_timezone");
	u(timezoneInput).parent().hide();
	if (timezoneInput && timezoneInput.value !== userTimezone) {
		timezoneInput.value = userTimezone;
		u(".datetimepicker").map((el) => {
			const val = el.value;
			if (val) {
				const date = new Date(
					`${val.substring(0, 10)}T${val.substring(11, 19)}Z`,
				);
				el.value = date.toLocaleString("sv");
			}
		});
	}

	const slugPrefix = u(
		`<br/><span id="id_slug-prefix" class="pe-2" style="color: #999">${window.local.clubUrl}</span>`,
	);
	u("#id_slug").before(slugPrefix);
	const slugPrefixWidth = document
		.getElementById("id_slug-prefix")
		.getBoundingClientRect().width;
	u("#id_slug").css({
		width: `calc(100% - ${slugPrefixWidth}px)`,
		"min-width": "150px",
		display: "inline-block",
	});
	u("#id_slug").closest(":has(.form-label)").find(".form-label").text("URL");
	const newSlug = u("#id_name").val() === "";
	let slugEdited = false;
	makeFieldRandomizable("#id_slug");
	u("#id_name").on("keyup", (e) => {
		if (!slugEdited) {
			const value = e.target.value;
			const slug = slugify(value, {
				strict: true,
				replacement: "-",
				trim: true,
			});
			u("#id_slug").val(slug.toLowerCase());
		}
	});
	u("#id_slug").on("blur", (e) => {
		slugEdited = e.target.value !== "";
	});
	if (newSlug) {
		u("#id_slug").val("");
	} else {
		slugEdited = true;
	}

	const REGEX_EMAIL =
		"([a-z0-9!#$%&'*+/=?^_`{|}~-]+(?:.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*@" +
		"(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?.)+[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)";

	new TomSelect("#id_emergency_contacts", {
		persist: false,
		maxItems: null,
		valueField: "email",
		delimiter: " ",
		render: {
			item: (item, escapeFunc) =>
				`<div>${
					item.email
						? `<span class="email">${escapeFunc(item.email)}</span>`
						: ""
				}</div>`,
			option: (item, escapeFunc) => {
				const label = item.email;
				return `<div><span class="label">${escapeFunc(label)}</span></div>`;
			},
		},
		createFilter: (input) => {
			const regexpA = new RegExp(`^${REGEX_EMAIL}$`, "i");
			return regexpA.test(input);
		},
		create: (input) => {
			if (new RegExp(`^${REGEX_EMAIL}$`, "i").test(input)) {
				return { email: input };
			}
			swal({
				title: "Error!",
				text: "Invalid email address.",
				type: "error",
				confirmButtonText: "OK",
			});
			return false;
		},
	});

	new TomSelect("#id_event_set", {
		allowEmptyOption: true,
		render: {
			option_create: (data, escapeFunc) =>
				`<div class="create">Create <strong>${escapeFunc(data.input)}</strong>&hellip;</div>`,
		},
		create: (input, callback) => {
			reqwest({
				url: `${window.local.apiBaseUrl}event-set`,
				method: "post",
				data: {
					club_slug: window.local.clubSlug,
					name: input,
				},
				type: "json",
				withCredentials: true,
				crossOrigin: true,
				headers: {
					"X-CSRFToken": window.local.csrfToken,
				},
				success: (res) => callback(res),
				error: () => callback(),
			});
		},
	});

	u('label[for$="-DELETE"]').parent(".form-group").hide();
	$(".formset_row").formset({
		addText: '<i class="fa-solid fa-circle-plus"></i> Add Competitor',
		addCssClass: "btn btn-info add-competitor-btn",
		deleteCssClass: "btn btn-danger delete-row",
		deleteText: '<i class="fa-solid fa-xmark"></i>',
		prefix: "competitors",
		added: onAddedCompetitorRow,
	});
	$(".extra_map_formset_row").formset({
		addText: '<i class="fa-solid fa-circle-plus"></i> Add Map',
		addCssClass: "btn btn-info add-map-btn",
		deleteCssClass: "btn btn-danger delete-row",
		deleteText: '<i class="fa-solid fa-xmark"></i>',
		prefix: "map_assignations",
		formCssClass: "extra_map_formset_row",
	});

	// next line must come after formset initialization
	u(".datetimepicker").map((el) => {
		makeTimeFieldClearable(el);
		makeFieldNowable(el);
		new tempusDominus.TempusDominus(el);
		el.autocomplete = "off";
		el.addEventListener(tempusDominus.Namespace.events.change, (e) => {
			showLocalTime(e.target);
		});
		showLocalTime(el);
	});
	const originalEventStart = u("#id_start_date").val();
	let competitorsStartTimeElsWithSameStartAsEvents = u(
		".competitor-table .datetimepicker",
	).filter(
		(el) => originalEventStart !== "" && el.value === originalEventStart,
	).nodes;

	u(competitorsStartTimeElsWithSameStartAsEvents).on(
		tempusDominus.Namespace.events.change,
		(ev) => {
			competitorsStartTimeElsWithSameStartAsEvents = u(
				competitorsStartTimeElsWithSameStartAsEvents,
			).filter((el) => {
				el.id !== ev.target.id;
			}).nodes;
		},
	);

	// next line must come after formset initialization
	let hasArchivedDevices = false;
	u('select[name$="-device"]').each((el) => {
		if (
			!hasArchivedDevices &&
			el.options[el.selectedIndex].text.endsWith("*")
		) {
			hasArchivedDevices = true;
		}
		new TomSelect(el, seletizeOptions);
	});

	if (hasArchivedDevices) {
		u(".add-competitor-btn")
			.parent()
			.append(
				'<div class="form-text"><span>* Archive of original device</span></div>',
			);
	}

	u("#csv_input").on("change", (e) => {
		const csvFile = e.target.files[0];
		const fileReader = new FileReader();
		fileReader.onload = () => {
			const csvStr = String.fromCharCode.apply(
				null,
				new Uint8Array(fileReader.result),
			);
			const encoding = jschardet.detect(csvStr).encoding;
			Papa.parse(csvFile, {
				complete: onCsvParsed,
				encoding,
			});
		};
		fileReader.readAsArrayBuffer(csvFile);
	});

	u("#iof_input").on("change", onIofXMLLoaded);

	u(".utc-offset").text(`(Timezone ${userTimezone})`);

	u("#id_start_date").on(tempusDominus.Namespace.events.change, (e) => {
		const o = competitorsStartTimeElsWithSameStartAsEvents;
		u(competitorsStartTimeElsWithSameStartAsEvents).each((el) => {
			el.value = e.target.value;
			u(el).trigger("change");
		});
		competitorsStartTimeElsWithSameStartAsEvents = o;
	});

	const tailLength = u("#id_tail_length").addClass("d-none").val();
	u('[for="id_tail_length"]').text("Tail length (Hours, Minutes, Seconds)");

	const tailLenFormDiv = u("<div/>").addClass("row", "g-1");

	const hourInput = u("<input/>")
		.addClass("d-inline-block")
		.addClass("form-control", "tailLengthControl")
		.css({ width: "85px" })
		.attr({
			type: "number",
			min: "0",
			max: "9999",
			name: "hours",
		})
		.val(Math.floor(tailLength / 3600));

	const hourDiv = u("<div/>")
		.addClass("col-auto")
		.append(hourInput)
		.append("<span> : </span>");

	const minuteInput = u("<input/>")
		.addClass("d-inline-block")
		.addClass("form-control", "tailLengthControl")
		.css({ width: "65px" })
		.attr({
			type: "number",
			min: "0",
			max: "59",
			name: "minutes",
		})
		.val(Math.floor(tailLength / 60) % 60);

	const minuteDiv = u("<div/>")
		.addClass("col-auto")
		.append(minuteInput)
		.append("<span> : </span>");

	const secondInput = u("<input/>")
		.addClass("d-inline-block")
		.addClass("form-control", "tailLengthControl")
		.css({ width: "65px" })
		.attr({
			type: "number",
			min: "0",
			max: "59",
			name: "seconds",
		})
		.val(tailLength % 60);

	const secondDiv = u("<div/>").addClass("col-auto").append(secondInput);

	tailLenFormDiv.append(hourDiv).append(minuteDiv).append(secondDiv);

	u("#id_tail_length").after(tailLenFormDiv);
	u(tailLenFormDiv)
		.find(".tailLengthControl")
		.on("input", (e) => {
			const commonDiv = u(e.target).closest("div:has(div input)");
			const hourInput = commonDiv.find('input[name="hours"]');
			const minInput = commonDiv.find('input[name="minutes"]');
			const secInput = commonDiv.find('input[name="seconds"]');
			const h = Number.parseInt(hourInput.val() || 0);
			const m = Number.parseInt(minInput.val() || 0);
			const s = Number.parseInt(secInput.val() || 0);
			const v = 3600 * h + 60 * m + s;
			if (Number.isNaN(v)) {
				return;
			}
			const tailLength = Math.max(0, v);
			u("#id_tail_length").val(tailLength);
			hourInput.val(Math.floor(tailLength / 3600));
			minInput.val(Math.floor((tailLength / 60) % 60));
			secInput.val(Math.floor(tailLength % 60));
		});

	u("#id_backdrop_map").parent().before("<hr/><h3>Maps</h3>");

	const currentGeoJson = u("#id_geojson_layer")
		.parent()
		.find("div div.col-auto a")
		.attr("href");
	if (currentGeoJson) {
		u("#id_geojson_layer")
			.parent()
			.find("div div.col-auto a")
			.text("Download")
			.after(
				`<a class="ms-2" href="https://map.routechoices.com/?geojson=${currentGeoJson}" target="_blank">Preview<a/>`,
			);
	}

	u("form").on("submit", (e) => {
		u("#submit-btn").attr({ disabled: true });
		u("button[name='save_continue']").addClass("disabled");
		u(e.submitter)
			.find("i")
			.removeClass("fa-floppy-disk")
			.addClass("fa-spinner fa-spin");
	});

	new bootstrap.Modal(document.getElementById("color-modal"));
	u(".color-input").each(createColorWidget);

	u(".tag-input").each(createTagWidget);

	if (window.performance) {
		const navEntries = window.performance.getEntriesByType("navigation");
		if (navEntries.length > 0 && navEntries[0].type === "back_forward") {
			location.reload();
		} else if (
			window.performance.navigation &&
			window.performance.navigation.type ===
				window.performance.navigation.TYPE_BACK_FORWARD
		) {
			location.reload();
		}
	}
})();

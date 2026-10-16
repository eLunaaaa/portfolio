const adminMessage =
  document.querySelector("#admin-message");

const adminVideoPanel =
  document.querySelector("#admin-video-panel");

const adminVideoEventId =
  document.querySelector("#admin-video-event-id");

const adminMergedVideo =
  document.querySelector("#admin-merged-video");

const closeAdminVideoButton =
  document.querySelector(
    "#close-admin-video-button",
  );


function renderAdminEvents(events) {
  const body =
    document.querySelector(
      "#admin-event-body"
    );

  if (events.length === 0) {
    body.innerHTML = `
      <tr>
        <td colspan="6">
          접수된 사고가 없습니다.
        </td>
      </tr>
    `;
    return;
  }

  body.innerHTML = events
    .map(
      (event) => `
        <tr>
          <td>${escapeHtml(event.event_id)}</td>
          <td>
            ${escapeHtml(
              event.accident_vehicle_id,
            )}
          </td>
          <td>
            ${escapeHtml(event.event_time_kst)}
          </td>
          <td>${event.evidence_count}</td>
          <td>
            <span class="status-badge">
              ${escapeHtml(event.merge_status)}
            </span>
          </td>
          <td>
            <button
              class="small-button"
              type="button"
              data-view-event="${
                escapeHtml(event.event_id)
              }"
              ${
                event.merged_available
                  ? ""
                  : "disabled"
              }
            >
              ${
                event.merged_available
                  ? "영상 보기"
                  : "병합 대기"
              }
            </button>

            <button
              class="danger-link"
              type="button"
              data-delete-event="${
                escapeHtml(event.event_id)
              }"
            >
              삭제
            </button>
          </td>
        </tr>
      `,
    )
    .join("");

  document
    .querySelectorAll(
      "[data-view-event]:not([disabled])"
    )
    .forEach((button) => {
      button.addEventListener(
        "click",
        () => openAdminVideo(
          button.dataset.viewEvent
        ),
      );
    });

  document
    .querySelectorAll(
      "[data-delete-event]"
    )
    .forEach((button) => {
      button.addEventListener(
        "click",
        () => deleteEvent(
          button.dataset.deleteEvent
        ),
      );
    });
}


function renderNodes(nodes, communication = null) {
  // 인증된 통신만 차량 상태에 반영하며, IP가 같다는 이유로 Health를 차량에 연결하지 않습니다.
  const now = Date.parse(communication?.server_time || new Date().toISOString());
  const recent = new Map((communication?.peers || []).filter(peer => peer.vehicle_id).map(peer => [peer.vehicle_id, peer]));
  for (const peer of recent.values()) {
    if (!nodes.some(node => node.vehicle_id === peer.vehicle_id)) {
      nodes = [...nodes, {vehicle_id: peer.vehicle_id, ip_address: peer.client_ip,
        last_seen_at: peer.last_seen_at, software_version: null}];
    }
  }
  nodes = nodes.map(node => {
    const peer = recent.get(node.vehicle_id);
    if (!peer || Date.parse(peer.last_seen_at) <= Date.parse(node.last_seen_at)) {
      return {...node, status: now - Date.parse(node.last_seen_at) <= 30000 ? "ONLINE" : "OFFLINE"};
    }
    return {...node, last_seen_at: peer.last_seen_at, ip_address: peer.client_ip,
      status: now - Date.parse(peer.last_seen_at) <= 30000 ? "ONLINE" : "OFFLINE"};
  });
  document.querySelector("#online-nodes").textContent = nodes.filter(node => node.status === "ONLINE").length;
  const body =
    document.querySelector(
      "#node-table-body"
    );

  if (nodes.length === 0) {
    body.innerHTML = `
      <tr>
        <td colspan="5">
          연결 기록이 없습니다.
        </td>
      </tr>
    `;
    return;
  }

  body.innerHTML = nodes
    .map(
      (node) => `
        <tr>
          <td>${escapeHtml(node.vehicle_id)}</td>
          <td>${escapeHtml(node.ip_address || "-")}</td>
          <td>
            ${escapeHtml(
              node.software_version || "-",
            )}
          </td>
          <td>${formatDate(node.last_seen_at)}</td>
          <td>
            <span class="connection-indicator ${
              node.status === "ONLINE"
                ? "is-online"
                : "is-offline"
            }">
              <i aria-hidden="true"></i>${node.status === "ONLINE" ? "온라인" : "최근 통신 없음"}
            </span>
          </td>
        </tr>
      `,
    )
    .join("");
}


function renderUsers(users) {
  const body =
    document.querySelector(
      "#user-table-body"
    );

  body.innerHTML = users
    .map(
      (user) => `
        <tr>
          <td>${escapeHtml(user.username)}</td>
          <td>
            ${escapeHtml(user.full_name || "-")}
          </td>
          <td>${escapeHtml(user.role)}</td>
          <td>${user.vehicle_count}</td>
          <td>
            ${user.is_active ? "활성" : "비활성"}
          </td>
          <td>
            ${
              user.role === "ADMIN"
                ? "-"
                : `
                  <button
                    class="danger-link"
                    data-delete-user-id="${user.id}"
                    data-delete-username="${
                      escapeHtml(user.username)
                    }"
                  >
                    삭제
                  </button>
                `
            }
          </td>
        </tr>
      `,
    )
    .join("");

  document
    .querySelectorAll(
      "[data-delete-user-id]"
    )
    .forEach((button) => {
      button.addEventListener(
        "click",
        () => deleteUser(
          Number(
            button.dataset.deleteUserId
          ),
          button.dataset.deleteUsername,
        ),
      );
    });
}


async function loadAdminOverview() {
  try {
    const overview = await apiRequest(
      "/api/v1/admin/overview"
    );

    document.querySelector(
      "#total-events"
    ).textContent =
      overview.total_events;

    document.querySelector(
      "#total-users"
    ).textContent =
      overview.total_users;

    document.querySelector(
      "#online-nodes"
    ).textContent =
      overview.online_nodes;

    renderAdminEvents(
      overview.events
    );

    renderNodes(
      overview.nodes,
      await apiRequest("/api/v1/admin/communication-logs?limit=1")
    );

    renderUsers(
      overview.users
    );

    setMessage(
      adminMessage,
      "서버 상태를 불러왔습니다.",
      "success",
    );
  } catch (error) {
    setMessage(
      adminMessage,
      error.message,
      "error",
    );

    if (
      error.message.includes("권한") ||
      error.message.includes("인증")
    ) {
      window.location.href = "/login";
    }
  }
}


function closeAdminVideo() {
  window.closeEvidenceViewer?.("admin-video-panel");
  adminMergedVideo.pause();
  adminMergedVideo.removeAttribute("src");
  adminMergedVideo.load();

  adminVideoPanel.classList.add(
    "hidden",
  );
}


function openAdminVideo(eventId) {
  const sourceButton = document.activeElement;
  const encodedEventId =
    encodeURIComponent(eventId);

  adminVideoEventId.textContent = eventId;

  adminMergedVideo.src =
    `/api/v1/admin/events/${
      encodedEventId
    }/video?cache=${Date.now()}`;

  adminMergedVideo.load();
  window.initializeVideoViews?.(adminMergedVideo,
    `/api/v1/admin/events/${encodedEventId}/video`);

  adminVideoPanel.classList.remove(
    "hidden",
  );

  window.openEvidenceViewer?.("admin-video-panel", sourceButton);

  setMessage(
    adminMessage,
    `${eventId} 병합 영상을 불러왔습니다.`,
    "success",
  );
}


async function deleteEvent(eventId) {
  const confirmation = window.prompt(
    `사고 ${eventId}를 삭제하려면 ` +
    `사고 ID를 정확히 입력하세요.`,
  );

  if (confirmation === null) {
    return;
  }

  try {
    const result = await apiRequest(
      `/api/v1/admin/events/${
        encodeURIComponent(eventId)
      }`,
      {
        method: "DELETE",
        body: JSON.stringify({
          confirmation,
        }),
      },
    );

    setMessage(
      adminMessage,
      result.message,
      "success",
    );

    await loadAdminOverview();
  } catch (error) {
    setMessage(
      adminMessage,
      error.message,
      "error",
    );
  }
}


async function deleteUser(
  userId,
  username,
) {
  const confirmation = window.prompt(
    `${username} 사용자를 삭제하려면 ` +
    `사용자 아이디를 정확히 입력하세요.`,
  );

  if (confirmation === null) {
    return;
  }

  try {
    const result = await apiRequest(
      `/api/v1/admin/users/${userId}`,
      {
        method: "DELETE",
        body: JSON.stringify({
          confirmation,
        }),
      },
    );

    setMessage(
      adminMessage,
      result.message,
      "success",
    );

    await loadAdminOverview();
  } catch (error) {
    setMessage(
      adminMessage,
      error.message,
      "error",
    );
  }
}


async function resetDemo() {
  const confirmation = window.prompt(
    "전체 시연 데이터를 초기화하려면 " +
    "RESET DEMO DATA를 입력하세요.",
  );

  if (confirmation === null) {
    return;
  }

  try {
    const result = await apiRequest(
      "/api/v1/admin/reset-demo",
      {
        method: "POST",
        body: JSON.stringify({
          confirmation,
        }),
      },
    );

    setMessage(
      adminMessage,
      result.message,
      "success",
    );

    await loadAdminOverview();
  } catch (error) {
    setMessage(
      adminMessage,
      error.message,
      "error",
    );
  }
}


async function purgeTrash() {
  const confirmation = window.prompt(
    "휴지통을 완전히 삭제하려면 " +
    "PURGE SERVER TRASH를 입력하세요. " +
    "이 작업은 복구할 수 없습니다.",
  );

  if (confirmation === null) {
    return;
  }

  try {
    const result = await apiRequest(
      "/api/v1/admin/trash/purge",
      {
        method: "POST",
        body: JSON.stringify({
          confirmation,
        }),
      },
    );

    setMessage(
      adminMessage,
      result.message,
      "success",
    );
  } catch (error) {
    setMessage(
      adminMessage,
      error.message,
      "error",
    );
  }
}


document
  .querySelector("#refresh-button")
  .addEventListener(
    "click",
    loadAdminOverview,
  );


closeAdminVideoButton.addEventListener(
  "click",
  closeAdminVideo,
);


document
  .querySelector("#logout-button")
  .addEventListener(
    "click",
    logoutAndRedirect,
  );


document
  .querySelector("#reset-demo-button")
  .addEventListener(
    "click",
    resetDemo,
  );


document
  .querySelector("#purge-trash-button")
  .addEventListener(
    "click",
    purgeTrash,
  );


loadAdminOverview();

setInterval(
  loadAdminOverview,
  5000,
);

import { Button } from "@heroui/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createPortTrafficBoard,
  deletePortTrafficBoard,
  fetchPortTrafficBoards,
} from "../../services/api";
import { queryKeys } from "../../constants/queryKeys";
import { useI18n } from "../../i18n";
import { useToast } from "../../hooks/useToast";
import { openPortTrafficBoardWindow } from "../../utils/portTrafficBoardWindow";
import { formatSystemTime } from "../../utils/time";

export function PortTrafficBoardListPage() {
  const { t } = useI18n();
  const { showOk, showError } = useToast();
  const queryClient = useQueryClient();

  const boardsQuery = useQuery({
    queryKey: queryKeys.portTrafficBoards,
    queryFn: fetchPortTrafficBoards,
    staleTime: 5000,
  });

  const createMut = useMutation({
    mutationFn: () =>
      createPortTrafficBoard({
        name: t("portTraffic.boardDefaultName"),
        cols: 2,
        panels: [],
      }),
    onSuccess: (created) => {
      showOk(t("portTraffic.boardCreated"));
      void queryClient.invalidateQueries({ queryKey: queryKeys.portTrafficBoards });
      openPortTrafficBoardWindow(created.id);
    },
    onError: (e: Error) => showError(e.message),
  });

  const deleteMut = useMutation({
    mutationFn: (boardId: string) => deletePortTrafficBoard(boardId),
    onSuccess: () => {
      showOk(t("portTraffic.boardDeleted"));
      void queryClient.invalidateQueries({ queryKey: queryKeys.portTrafficBoards });
    },
    onError: (e: Error) => showError(e.message),
  });

  const boards = boardsQuery.data?.items || [];

  return (
    <section className="panel nm-page-panel">
      <div className="panel__toolbar">
        <h2>{t("portTraffic.wallTitle")}</h2>
        <div className="btn-row">
          <Button
            size="sm"
            variant="primary"
            isDisabled={createMut.isPending}
            onPress={() => createMut.mutate()}
          >
            {t("portTraffic.boardCreate")}
          </Button>
        </div>
      </div>

      <div className="pt-list">
        {boardsQuery.isLoading ? (
          <p className="muted">…</p>
        ) : boardsQuery.isError ? (
          <p className="error">{(boardsQuery.error as Error)?.message || t("common.opFailed")}</p>
        ) : !boards.length ? (
          <div className="pt-list-empty">
            <p>{t("portTraffic.boardEmpty")}</p>
            <Button
              size="sm"
              variant="primary"
              isDisabled={createMut.isPending}
              onPress={() => createMut.mutate()}
            >
              {t("portTraffic.boardCreate")}
            </Button>
          </div>
        ) : (
          <div className="pt-list-table-wrap">
            <table className="data-table pt-list-table">
              <thead>
                <tr>
                  <th>{t("portTraffic.boardName")}</th>
                  <th>{t("portTraffic.boardPanelCount")}</th>
                  <th>{t("portTraffic.boardCols")}</th>
                  <th>{t("portTraffic.boardUpdated")}</th>
                  <th>{t("portTraffic.col.actions")}</th>
                </tr>
              </thead>
              <tbody>
                {boards.map((b) => (
                  <tr key={b.id}>
                    <td>
                      <Button
                        size="sm"
                        variant="ghost"
                        className="link-btn pt-list-task-name"
                        onPress={() => openPortTrafficBoardWindow(b.id)}
                      >
                        {b.name || t("portTraffic.boardDefaultName")}
                      </Button>
                    </td>
                    <td>{b.panel_count}</td>
                    <td>{b.cols}</td>
                    <td>{b.updated_at ? formatSystemTime(b.updated_at) : "—"}</td>
                    <td>
                      <div className="btn-row table-actions">
                        <Button
                          size="sm"
                          variant="ghost"
                          onPress={() => openPortTrafficBoardWindow(b.id)}
                        >
                          {t("portTraffic.boardOpen")}
                        </Button>
                        <Button
                          size="sm"
                          variant="danger"
                          isDisabled={deleteMut.isPending}
                          onPress={() => {
                            if (window.confirm(t("portTraffic.boardConfirmDelete"))) {
                              deleteMut.mutate(b.id);
                            }
                          }}
                        >
                          {t("portTraffic.delete")}
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  );
}

// The page a shared link opens. It is the dialog and nothing else: no composer, no tools, no
// files. A guest is not signed in, and a signed-in operator who opens the link sees this page
// too — the link is a page, not a way into the app.

import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { api, ApiError, type MediaPresentation } from "../api";
import { timeAgo } from "../components";
import { t, useLang } from "../i18n";
import { renderMarkdown } from "../md";
import { splitMediaAnswer, type AnswerPart } from "../mediaformat";

type SharedMessage = { role: "user" | "assistant"; text: string; at: string; via?: string; media?: MediaPresentation[] };

type SharedPage = { title: string; older: boolean; messages: SharedMessage[] };

const REFRESH_MS = 20_000;

function Md({ text, className }: { text: string; className?: string }) {
  const html = useMemo(() => renderMarkdown(text), [text]);
  return <div className={className} dangerouslySetInnerHTML={{ __html: html }} />;
}

function albumRatio(items: MediaPresentation["items"]): string | undefined {
  const photo = items.find((item) => (item.kind === "image" || item.kind === "animation") && item.width && item.height);
  return photo?.width && photo.height ? `${photo.width} / ${photo.height}` : undefined;
}

function Pictures({ presentation }: { presentation: MediaPresentation }) {
  const ratio = presentation.layout === "album" ? albumRatio(presentation.items) : undefined;
  return (
    <div className={`inline-media ${presentation.layout} items-${Math.min(4, presentation.items.length)}`} style={ratio ? ({ "--album-ratio": ratio } as CSSProperties) : undefined}>
      <div className="inline-media-track">
        {presentation.items.map((item) =>
          item.kind === "audio" ? (
            <audio key={item.id} className="shared-audio" src={item.url} controls preload="metadata" />
          ) : (
            <figure key={item.id} className="inline-media-image">
              {item.kind === "video" ? <video src={item.url} controls playsInline preload="metadata" /> : <img src={item.url} alt={item.alt} />}
              {item.caption ? <figcaption>{item.caption}</figcaption> : null}
            </figure>
          ),
        )}
      </div>
    </div>
  );
}

function Answer({ message }: { message: SharedMessage }) {
  const parts = splitMediaAnswer(message.text, message.media ?? []);
  if (parts.length === 1 && parts[0].kind === "text") return <Md className="answer" text={message.text} />;
  return (
    <div className="answer answer-with-media">
      {parts.map((part: AnswerPart, index) =>
        part.kind === "text" ? <Md key={`text-${index}`} text={part.text} /> : <Pictures key={part.presentation.id} presentation={part.presentation} />,
      )}
    </div>
  );
}

export function SharedDialog({ slug }: { slug: string }) {
  useLang();
  const [page, setPage] = useState<SharedPage | null>(null);
  const [error, setError] = useState<number | null>(null);
  const had = useRef(false);

  useEffect(() => {
    const tag = document.createElement("meta");
    tag.name = "robots";
    tag.content = "noindex, nofollow";
    document.head.appendChild(tag);
    return () => tag.remove();
  }, []);

  useEffect(() => {
    let stopped = false;
    const load = () => {
      api
        .get<SharedPage>(`/c/${encodeURIComponent(slug)}/transcript`)
        .then((next) => {
          if (stopped) return;
          had.current = true;
          setPage(next);
          setError(null);
          document.title = next.title || t("share.page.kicker");
        })
        .catch((reason: unknown) => {
          if (stopped) return;
          const status = reason instanceof ApiError ? reason.status : 0;
          // A later refresh that merely failed to connect leaves the page that is already open.
          // A refusal does not: the link was turned off, or it never had its key.
          if (status === 404 || status === 403 || !had.current) {
            had.current = false;
            setPage(null);
            setError(status || 500);
            document.title = t("share.page.kicker");
          }
        });
    };
    load();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") load();
    }, REFRESH_MS);
    const onVisible = () => {
      if (document.visibilityState === "visible") load();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      stopped = true;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [slug]);

  const title = page?.title || t("share.page.kicker");
  return (
    <div className="shared">
      <div className="shared-column">
        <header className="shared-head">
          <div className="shared-titles">
            <div className="shared-kicker">{t("share.page.kicker")}</div>
            <h1>{title}</h1>
          </div>
        </header>
        <div className="shared-scroll">
          {error === 404 && <div className="empty"><b>{t("share.page.gone")}</b></div>}
          {error === 403 && <div className="empty"><b>{t("share.page.locked")}</b></div>}
          {error !== null && error !== 404 && error !== 403 && <div className="empty"><b>{t("share.page.failed")}</b></div>}
          {error === null && page && page.messages.length === 0 && <div className="empty"><b>{t("share.page.empty")}</b></div>}
          {error === null && !page && <div className="empty">{t("common.loading")}</div>}
          {page?.older && <div className="sub older-note">{t("share.page.older")}</div>}
          {page?.messages.map((message, index) => (
            <article key={`${message.at}-${index}`} className="turn">
              {message.role === "user" ? (
                <div className="msg user">
                  {message.via ? <span className="msg-origin">{t("turn.origin.inbound", { source: message.via })}</span> : null}
                  <Md text={message.text} />
                  {message.at ? <time className="shared-time" dateTime={message.at}>{timeAgo(message.at)}</time> : null}
                </div>
              ) : (
                <>
                  <Answer message={message} />
                  {message.at ? <time className="shared-time" dateTime={message.at}>{timeAgo(message.at)}</time> : null}
                </>
              )}
            </article>
          ))}
        </div>
      </div>
    </div>
  );
}

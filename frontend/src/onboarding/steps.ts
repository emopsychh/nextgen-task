export type TourPlacement = "top" | "bottom" | "left" | "right";

export type TourStep = {
  id: string;
  /** data-tour attribute value; null = centered tip */
  target: string | null;
  title: string;
  body: string;
  placement?: TourPlacement;
  /** Navigate before showing this step */
  route?: string | "home" | "client-workspace";
};

export const AGENCY_STEPS: TourStep[] = [
  {
    id: "welcome",
    target: null,
    title: "Добро пожаловать в кабинет агентства",
    body: "Короткий тур подсветит реальные кнопки и панели. Можно пропускать шаги или закрыть обучение целиком.",
  },
  {
    id: "rail",
    target: "tour-client-rail",
    title: "Рельса клиентов",
    body: "Здесь все привязанные клиентские порталы. Переключайтесь между ними одним кликом.",
    placement: "right",
    route: "home",
  },
  {
    id: "add-client",
    target: "tour-add-client",
    title: "Добавить клиента",
    body: "Плюс открывает экран подключения. Сначала клиент ставит приложение и выбирает роль «Клиент».",
    placement: "right",
    route: "home",
  },
  {
    id: "connect",
    target: "tour-connect-client",
    title: "Привязка портала",
    body: "Выберите портал и нажмите «Подключить». После этого клиент появится в рельсе слева.",
    placement: "bottom",
    route: "home",
  },
  {
    id: "workspace",
    target: "tour-new-project",
    title: "Рабочее пространство клиента",
    body: "В кабинете клиента создавайте проекты — модули работ. Кнопка «Новый проект» всегда сверху.",
    placement: "bottom",
    route: "client-workspace",
  },
  {
    id: "sidebar",
    target: "tour-sidebar",
    title: "Проекты и лента",
    body: "В боковой панели — лента активности и список проектов. Отсюда быстро попадаете в задачи.",
    placement: "right",
    route: "client-workspace",
  },
  {
    id: "activity",
    target: "tour-activity-feed",
    title: "Лента изменений",
    body: "Новые задачи, комментарии и смены статусов появляются здесь — общий пульс по клиенту.",
    placement: "left",
    route: "client-workspace",
  },
  {
    id: "tasks-hint",
    target: "tour-sidebar",
    title: "Задачи и статусы",
    body: "Откройте проект → «Новая задача». Статус («Начать», «Завершить») двигаете вы — клиент ставит задачу агентству.",
    placement: "right",
    route: "client-workspace",
  },
  {
    id: "done",
    target: null,
    title: "Готово — можно работать",
    body: "Привяжите клиента, создайте проект и задачу. Чат и синхронизация с Bitrix подхватятся сами.",
  },
];

export const CLIENT_STEPS: TourStep[] = [
  {
    id: "welcome",
    target: null,
    title: "Добро пожаловать",
    body: "Это ваше пространство для задач агентству. Тур покажет кнопки, которыми вы будете пользоваться каждый день.",
  },
  {
    id: "sidebar",
    target: "tour-sidebar",
    title: "Ваши проекты",
    body: "Слева — лента и список проектов. Отсюда возвращаетесь к любой работе.",
    placement: "right",
    route: "home",
  },
  {
    id: "new-project",
    target: "tour-new-project",
    title: "Новый проект",
    body: "Создайте модуль работ — сайт, реклама, интеграция. Агентство увидит тот же проект.",
    placement: "bottom",
    route: "home",
  },
  {
    id: "activity",
    target: "tour-activity-feed",
    title: "Лента активности",
    body: "Здесь видно, когда команда взяла задачу в работу, ответила в чате или сдвинула срок.",
    placement: "left",
    route: "home",
  },
  {
    id: "recent",
    target: "tour-recent-projects",
    title: "Недавние проекты",
    body: "Быстрый вход в проекты с прогрессом. Внутри — кнопка «Новая задача» для постановки работ агентству.",
    placement: "right",
    route: "home",
  },
  {
    id: "status-note",
    target: "tour-new-project",
    title: "Статусы — на стороне агентства",
    body: "Вы описываете задачу и срок. «Начать» и «Завершить» нажимает команда — вам придёт событие в чат.",
    placement: "bottom",
    route: "home",
  },
  {
    id: "done",
    target: null,
    title: "Можно начинать",
    body: "Создайте проект, поставьте задачу и напишите в чат — агентство подхватит.",
  },
];

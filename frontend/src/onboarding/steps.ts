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
    body: "Плюс открывает экран подключения. Клиент ставит то же приложение на своём портале — роль «Клиент» назначается автоматически.",
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
    body: "В кабинете клиента создавайте проекты — модули работ. В Bitrix это задачи внутри проекта компании.",
    placement: "bottom",
    route: "client-workspace",
  },
  {
    id: "sidebar",
    target: "tour-sidebar",
    title: "Проекты слева",
    body: "В боковой панели — список проектов. Отсюда быстро попадаете в задачи.",
    placement: "right",
    route: "client-workspace",
  },
  {
    id: "focus",
    target: "tour-agency-focus",
    title: "Что важно сейчас",
    body: "Здесь пакет часов, оспоренные отчёты, задачи от клиента и то, что горит по срокам.",
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
    body: "Слева — список проектов. Проекты создаёт агентство; вы работаете с задачами внутри них.",
    placement: "right",
    route: "home",
  },
  {
    id: "hours",
    target: "tour-deal-hours",
    title: "Пакет часов",
    body: "Сверху видно, сколько часов осталось в пакете сопровождения.",
    placement: "bottom",
    route: "home",
  },
  {
    id: "waiting",
    target: "tour-waiting-for-you",
    title: "Ждёт вас",
    body: "Сверху пакет часов. Ниже два столбца: отчёты на согласование и недавно завершённые задачи.",
    placement: "top",
    route: "home",
  },
  {
    id: "status-note",
    target: "tour-sidebar",
    title: "Статусы — на стороне агентства",
    body: "Вы описываете задачу и срок. «Начать» и «Завершить» нажимает команда.",
    placement: "right",
    route: "home",
  },
  {
    id: "done",
    target: null,
    title: "Можно начинать",
    body: "Откройте проект слева и создайте задачу, когда нужна работа от команды.",
  },
];
